"""HTTP API for the certificate equivalency agent.

A thin layer over agent.orchestrator.run_turn so the agent can be driven
over HTTP (load tests, integrations) and not only through the Streamlit
UI. Sessions (CaseState + conversation) are held in memory per process,
keyed by session_id, the same way the Streamlit UI keeps them in
st.session_state -- so run a single worker process; concurrency comes from
FastAPI's thread pool running each turn.

    uvicorn api.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import threading
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.orchestrator import run_turn
from schemas import CaseState

app = FastAPI(title="Certificate Equivalency Agent API", version="1")

_sessions: dict[str, tuple[CaseState, list[dict], threading.Lock]] = {}
_sessions_lock = threading.Lock()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tools_called: list[str]
    escalated: bool
    ticket_id: str | None
    duration_ms: float


def _session(session_id: str) -> tuple[CaseState, list[dict], threading.Lock]:
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = (CaseState(), [], threading.Lock())
        return _sessions[session_id]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "sessions": len(_sessions)}


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """One user turn. A sync endpoint on purpose: run_turn blocks on model
    calls, and FastAPI runs sync endpoints in its thread pool."""
    session_id = request.session_id or uuid4().hex
    case_state, conversation, lock = _session(session_id)
    with lock:  # turns within one session are sequential, as in the UI
        turn_start = len(case_state.tool_history)
        started = time.perf_counter()
        try:
            reply = run_turn(case_state, conversation, request.message)
        except RuntimeError as exc:  # e.g. missing model configuration
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        duration_ms = (time.perf_counter() - started) * 1000
    return ChatResponse(
        session_id=session_id,
        reply=reply,
        tools_called=[t.tool for t in case_state.tool_history[turn_start:]],
        escalated=case_state.escalation is not None,
        ticket_id=case_state.escalation.ticket_id if case_state.escalation else None,
        duration_ms=round(duration_ms, 1),
    )


@app.delete("/api/v1/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    with _sessions_lock:
        if _sessions.pop(session_id, None) is None:
            raise HTTPException(status_code=404, detail="Unknown session_id")
