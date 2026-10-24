"""HTTP API for the certificate equivalency agent.

A thin layer over agent.orchestrator.run_turn so the agent can be driven
over HTTP (load tests, integrations) and not only through the Streamlit
UI. Every endpoint except /health needs a bearer token (api/auth.py), which
identifies one applicant. Sessions (CaseState + conversation) are issued
by the server, bound to that applicant, and held in a bounded in-memory
store; chat turns are rate limited per applicant (api/limits.py) -- so run
a single worker process; concurrency comes from FastAPI's thread pool
running each turn.

    uvicorn api.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
import secrets
import threading
import time

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.orchestrator import run_turn
from api.auth import authenticated_applicant
from api.limits import RateLimiter, SessionStore
from schemas import CaseState

app = FastAPI(title="Certificate Equivalency Agent API", version="1")


class _Session:
    def __init__(self, owner: str):
        # The session can only ever act on its owner's case (enforced in the orchestrator).
        self.case_state = CaseState(authorized_applicant_id=owner)
        self.conversation: list[dict] = []
        self.turns = 0
        self.lock = threading.Lock()


_sessions: SessionStore[_Session] = SessionStore.from_env()
_rate_limiter = RateLimiter.from_env()


def _max_turns() -> int:
    try:
        return int(os.environ.get("MOE_MAX_TURNS_PER_SESSION", 50))
    except ValueError:
        return 50


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Omit to start a new session; pass the session_id from an earlier response to continue it.
    session_id: str | None = Field(default=None, max_length=128)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tools_called: list[str]
    escalated: bool
    ticket_id: str | None
    duration_ms: float


def rate_limited_applicant(applicant: str = Depends(authenticated_applicant)) -> str:
    retry_after = _rate_limiter.acquire(applicant)
    if retry_after is not None:
        raise HTTPException(status_code=429, detail="Too many requests; slow down.", headers={"Retry-After": str(int(retry_after))})
    return applicant


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest, applicant: str = Depends(rate_limited_applicant)) -> ChatResponse:
    """One user turn. A sync endpoint on purpose: run_turn blocks on model
    calls, and FastAPI runs sync endpoints in its thread pool."""
    if request.session_id is None:
        session_id = secrets.token_urlsafe(32)
        session = _Session(applicant)
        _sessions.add(session_id, applicant, session)
    else:
        session_id = request.session_id
        session = _sessions.get(session_id, applicant)
        if session is None:  # unknown, expired, evicted, or someone else's -- indistinguishable on purpose
            raise HTTPException(status_code=404, detail="Unknown session_id.")

    with session.lock:  # turns within one session are sequential, as in the UI
        if session.turns >= _max_turns():
            raise HTTPException(status_code=409, detail="This session has reached its turn limit; start a new session.")
        session.turns += 1
        case_state = session.case_state
        turn_start = len(case_state.tool_history)
        started = time.perf_counter()
        try:
            reply = run_turn(case_state, session.conversation, request.message)
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
def delete_session(session_id: str, applicant: str = Depends(authenticated_applicant)) -> None:
    if not _sessions.remove(session_id, applicant):
        raise HTTPException(status_code=404, detail="Unknown session_id.")
