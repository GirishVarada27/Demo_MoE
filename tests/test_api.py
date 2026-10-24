"""Tests for the HTTP API (api/server.py) and MOE_DATA_URL data-dir resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.server as server
from tools._data_dir import DEFAULT_DATA_DIR, data_dir


def _fake_run_turn(case_state, conversation, message):
    from datetime import datetime, timezone

    from schemas import ToolCallTrace

    case_state.tool_history.append(
        ToolCallTrace(step=1, tool="get_applicant_record", args={}, result_summary="ok", timestamp=datetime.now(timezone.utc))
    )
    conversation.append({"role": "user", "content": message})
    return f"echo: {message} (turn {len(conversation)})"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "run_turn", _fake_run_turn)
    monkeypatch.setattr(server, "_sessions", {})
    return TestClient(server.app)


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_chat_creates_session_and_keeps_it_across_turns(client):
    first = client.post("/api/v1/chat", json={"message": "hi"}).json()
    second = client.post("/api/v1/chat", json={"message": "again", "session_id": first["session_id"]}).json()

    assert first["reply"] == "echo: hi (turn 1)"
    assert second["reply"] == "echo: again (turn 2)"  # same conversation, not a fresh one
    assert second["tools_called"] == ["get_applicant_record"]
    assert second["escalated"] is False


def test_chat_rejects_empty_message(client):
    assert client.post("/api/v1/chat", json={"message": ""}).status_code == 422


def test_chat_returns_503_when_model_config_missing(client, monkeypatch):
    def broken(*args):
        raise RuntimeError("Missing required environment variables")

    monkeypatch.setattr(server, "run_turn", broken)
    response = client.post("/api/v1/chat", json={"message": "hi"})
    assert response.status_code == 503


def test_delete_session(client):
    session_id = client.post("/api/v1/chat", json={"message": "hi"}).json()["session_id"]
    assert client.delete(f"/api/v1/sessions/{session_id}").status_code == 204
    assert client.delete(f"/api/v1/sessions/{session_id}").status_code == 404


def test_data_dir_defaults_to_repo_data(monkeypatch):
    monkeypatch.delenv("MOE_DATA_URL", raising=False)
    assert data_dir() == DEFAULT_DATA_DIR


def test_data_dir_reads_windows_file_url(monkeypatch):
    monkeypatch.setenv("MOE_DATA_URL", "file:///D:/perf-data/moe")
    assert data_dir() == Path("D:/perf-data/moe")


def test_data_dir_rejects_non_file_url(monkeypatch):
    monkeypatch.setenv("MOE_DATA_URL", "postgres://db/moe")
    with pytest.raises(RuntimeError):
        data_dir()
