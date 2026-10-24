"""Tests for the API's resource limits (api/limits.py and their use in
api/server.py) and for concurrent writes to the JSON stores (tools/_json_store.py)."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

import api.server as server
import tools.draft_application as drafts
import tools.escalation as escalation
from api.auth import token_hash
from api.limits import RateLimiter, SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- rate limiter -------------------------------------------------------------------


def test_rate_limiter_allows_burst_then_throttles_and_refills():
    clock = Clock()
    limiter = RateLimiter(per_minute=60, burst=3, clock=clock)
    assert [limiter.acquire("A") for _ in range(3)] == [None, None, None]
    assert limiter.acquire("A") == 1  # one token per second at 60/min
    clock.now += 1.0
    assert limiter.acquire("A") is None


def test_rate_limits_are_per_applicant():
    limiter = RateLimiter(per_minute=60, burst=1, clock=Clock())
    assert limiter.acquire("A") is None and limiter.acquire("A") is not None
    assert limiter.acquire("B") is None  # B isn't affected by A


# --- session store ------------------------------------------------------------------


def test_sessions_expire_after_idle_time():
    clock = Clock()
    store = SessionStore(idle_seconds=60, per_owner=5, total=100, clock=clock)
    store.add("s1", "A", "session-1")
    clock.now += 30
    assert store.get("s1", "A") == "session-1"  # use refreshes the idle timer
    clock.now += 59
    assert store.get("s1", "A") == "session-1"
    clock.now += 61
    assert store.get("s1", "A") is None and len(store) == 0


def test_per_applicant_cap_evicts_that_applicants_least_recently_used():
    store = SessionStore(idle_seconds=1800, per_owner=2, total=100, clock=Clock())
    store.add("a1", "A", 1)
    store.add("a2", "A", 2)
    store.add("b1", "B", 3)
    store.get("a1", "A")  # a1 is now more recent than a2
    store.add("a3", "A", 4)
    assert store.get("a2", "A") is None  # evicted
    assert store.get("a1", "A") == 1 and store.get("a3", "A") == 4 and store.get("b1", "B") == 3


def test_global_cap_evicts_least_recently_used():
    store = SessionStore(idle_seconds=1800, per_owner=10, total=2, clock=Clock())
    store.add("s1", "A", 1)
    store.add("s2", "B", 2)
    store.add("s3", "C", 3)
    assert store.get("s1", "A") is None and len(store) == 2


def test_store_never_returns_or_removes_another_owners_session():
    store = SessionStore(idle_seconds=1800, per_owner=5, total=100, clock=Clock())
    store.add("s1", "A", 1)
    assert store.get("s1", "B") is None and store.remove("s1", "B") is False
    assert store.get("s1", "A") == 1


# --- limits through the API ---------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOE_API_TOKENS", json.dumps({token_hash("tok-a"): "APP-001", token_hash("tok-b"): "APP-002"}))
    monkeypatch.setattr(server, "run_turn", lambda case_state, conversation, message: "ok")
    monkeypatch.setattr(server, "_sessions", SessionStore(idle_seconds=1800, per_owner=2, total=100))
    monkeypatch.setattr(server, "_rate_limiter", RateLimiter(per_minute=1, burst=3))
    return TestClient(server.app)


A, B = {"Authorization": "Bearer tok-a"}, {"Authorization": "Bearer tok-b"}


def test_chat_returns_429_with_retry_after_once_the_burst_is_used(client):
    statuses = [client.post("/api/v1/chat", json={"message": "hi"}, headers=A).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    throttled = client.post("/api/v1/chat", json={"message": "hi"}, headers=A)
    assert int(throttled.headers["retry-after"]) > 0
    assert client.post("/api/v1/chat", json={"message": "hi"}, headers=B).status_code == 200  # other applicant unaffected


def test_anonymous_requests_are_rejected_before_spending_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/api/v1/chat", json={"message": "hi"}, headers=A).status_code == 200


def test_evicted_session_returns_404(client, monkeypatch):
    monkeypatch.setattr(server, "_rate_limiter", RateLimiter(per_minute=6000, burst=1000))
    first = client.post("/api/v1/chat", json={"message": "hi"}, headers=A).json()["session_id"]
    client.post("/api/v1/chat", json={"message": "hi"}, headers=A)
    client.post("/api/v1/chat", json={"message": "hi"}, headers=A)  # per_owner=2: first is evicted
    assert client.post("/api/v1/chat", json={"message": "x", "session_id": first}, headers=A).status_code == 404


def test_turn_limit_per_session(client, monkeypatch):
    monkeypatch.setattr(server, "_rate_limiter", RateLimiter(per_minute=6000, burst=1000))
    monkeypatch.setenv("MOE_MAX_TURNS_PER_SESSION", "2")
    sid = client.post("/api/v1/chat", json={"message": "1"}, headers=A).json()["session_id"]
    assert client.post("/api/v1/chat", json={"message": "2", "session_id": sid}, headers=A).status_code == 200
    blocked = client.post("/api/v1/chat", json={"message": "3", "session_id": sid}, headers=A)
    assert blocked.status_code == 409 and "start a new session" in blocked.json()["detail"]


# --- JSON store races ---------------------------------------------------------------------


def _concurrently(n: int, fn) -> list:
    barrier, results = threading.Barrier(n), []

    def run(i):
        barrier.wait()
        results.append(fn(i))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_concurrent_escalations_are_all_kept():
    results = _concurrently(30, lambda i: escalation.escalate_to_human(f"reason {i}", "summary"))
    assert all(r.error is None for r in results)
    stored = json.loads(escalation._QUEUE_PATH.read_text(encoding="utf-8"))
    assert sorted(t["reason"] for t in stored) == sorted(f"reason {i}" for i in range(30))


def test_concurrent_notes_on_one_ticket_are_all_kept():
    ticket = escalation.escalate_to_human("first", "summary").ticket
    results = _concurrently(20, lambda i: escalation.append_to_ticket(ticket.ticket_id, f"note {i}"))
    assert all(r.error is None for r in results)
    stored = json.loads(escalation._QUEUE_PATH.read_text(encoding="utf-8"))
    assert len(stored) == 1 and len(stored[0]["updates"]) == 20


def test_concurrent_drafts_are_all_kept():
    results = _concurrently(20, lambda i: drafts.create_draft_application("APP-001"))
    assert all(r.error is None for r in results)
    assert len(json.loads(drafts._STORE_PATH.read_text(encoding="utf-8"))) == 20
