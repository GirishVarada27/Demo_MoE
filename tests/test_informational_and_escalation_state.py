"""Regression tests for the second round of "testing APP-006" fixes:

1. A generic informational question (e.g. "what documents do I need") must
   never trigger escalate_to_human or create a ticket, even when validation
   keeps failing -- agent.orchestrator._is_informational_turn/
   _build_informational_fallback answer from the retrieved chunks instead,
   state what couldn't be confirmed, and offer escalation in words only.
2. Escalation state (ticket_id, status, reason, created_at) now lives on
   CaseState.escalation (an EscalationTicket), injected into the model's
   context every turn via the existing case-state JSON dump. Once a ticket
   exists, a later answer must reference it by id --
   agent.validation.check_existing_escalation_referenced rejects a draft
   that discusses escalation without citing the existing ticket -- and the
   model calling escalate_to_human again gets redirected (append_to_ticket)
   rather than filing a second ticket.

Point 3 (Verified statements rejecting a tool-result citation) is covered
in tests/test_validation.py; point 5 (every failed validate_draft step
logged with its exact failure reason) was already covered by
_log_validation_failure before this round and is exercised incidentally by
every scenario here.
"""

from __future__ import annotations

import json

from agent.orchestrator import run_turn
from agent.validation import check_existing_escalation_referenced
from schemas import CaseState
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create


def _tool_names(case_state: CaseState, start: int = 0) -> list[str]:
    return [entry.tool for entry in case_state.tool_history[start:]]


# --- 1. Informational questions never escalate, never create a ticket -------


def test_documents_question_never_creates_a_ticket_even_when_validation_keeps_failing(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()

    # Ungrounded both times -- claims something under Verified without citing any retrieved
    # chunk id, so validation fails on both the first attempt and the revision.
    bad_draft = (
        "Verified: Based on the official knowledge base, you need a transcript.\n"
        "Assumed: None.\nSimulated: None.\nNext step: none."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(tool_calls=[FakeToolCall("c1", "search_requirements", {"query": "What documents are required?"})])
            ),
            FakeResponse(FakeMessage(content=bad_draft)),
            FakeResponse(FakeMessage(content=bad_draft)),
            FakeResponse(FakeMessage(content=bad_draft)),
        ]
    )

    result = run_turn(case_state, [], "What documents do I need?", client=object(), model="fake-model", create_fn=create_fn)

    tool_names = _tool_names(case_state)
    assert "escalate_to_human" not in tool_names
    assert case_state.facts.get("escalation_ticket_id") is None
    assert case_state.escalation is None
    assert not (tmp_path / "escalation_queue.json").exists()  # no ticket ever persisted

    # It still answers from what was actually retrieved, and offers escalation in words only.
    assert "attested" in result.lower() or "attestation" in result.lower()  # from the real attest-001 chunk
    assert "human reviewer" in result.lower()


def test_documents_question_model_calling_escalate_to_human_directly_is_declined(tmp_path, monkeypatch):
    """Even if the model itself (wrongly) chooses to call escalate_to_human
    for a question with no applicant on file, the orchestrator declines it
    rather than filing a ticket."""
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none."
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(tool_calls=[FakeToolCall("c1", "search_requirements", {"query": "What's the fee?"})])
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c2",
                            "escalate_to_human",
                            {"reason": "Not sure about the fee", "case_summary": "generic question"},
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, [], "What's the fee?", client=object(), model="fake-model", create_fn=create_fn)

    assert case_state.escalation is None
    assert case_state.facts.get("escalation_ticket_id") is None
    assert not (tmp_path / "escalation_queue.json").exists()
    declined_entry = next(t for t in case_state.tool_history if t.tool == "escalate_to_human")
    assert "declined" in declined_entry.result_summary


# --- 2. Escalation state is structural, injected every turn, never duplicated ---


def test_second_turn_after_escalation_references_existing_ticket_not_a_new_one(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()

    # Turn 1: a tool failure forces the first real escalation.
    create_fn_1 = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-001", "simulate_failure": True})]
                )
            ),
            FakeResponse(FakeMessage(content="Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none.")),
        ]
    )
    run_turn(case_state, [], "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn_1)

    assert case_state.escalation is not None  # structured state, not just a bare id in facts
    assert case_state.escalation.status == "queued"
    assert case_state.escalation.reason
    assert case_state.escalation.created_at is not None
    ticket_id = case_state.escalation.ticket_id
    steps_after_turn1 = len(case_state.tool_history)

    # Turn 2: the model correctly references the existing ticket instead of proposing a new one.
    turn2_draft = (
        "Verified: None.\nAssumed: None.\n"
        f"Simulated: Your case is already with a human reviewer under ticket {ticket_id}.\n"
        f"Next step: A human reviewer will follow up on ticket {ticket_id}; no further action is "
        "needed from you right now."
    )
    create_fn_2 = dispatching_create([FakeResponse(FakeMessage(content=turn2_draft))])

    result = run_turn(case_state, [], "Any update on my case?", client=object(), model="fake-model", create_fn=create_fn_2)

    assert result == turn2_draft  # validated on the first try -- no revision, no new ticket
    assert ticket_id in result
    assert len(case_state.tool_history) == steps_after_turn1  # no new tool calls this turn

    queue = json.loads((tmp_path / "escalation_queue.json").read_text(encoding="utf-8"))
    assert len(queue) == 1  # still exactly one ticket
    assert queue[0]["ticket_id"] == ticket_id


def test_existing_escalation_referenced_rejects_a_proposal_to_escalate_again():
    """Unit-level: once CaseState.escalation is set, a draft that discusses
    escalation without citing the existing ticket id is rejected -- it
    reads as a fresh proposal to escalate, not a reference to what's
    already open."""
    from datetime import datetime, timezone

    from schemas import EscalationTicket

    case_state = CaseState(
        escalation=EscalationTicket(
            ticket_id="TCK-EXISTING1",
            reason="prior issue",
            case_summary="summary",
            created_at=datetime.now(timezone.utc),
        )
    )
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: Would you like me to escalate this to a human reviewer?"

    failures = check_existing_escalation_referenced(draft, case_state)

    assert failures
    assert "TCK-EXISTING1" in failures[0]


def test_model_escalating_again_is_redirected_to_append_not_a_new_ticket(tmp_path, monkeypatch):
    """If the model itself calls escalate_to_human again on a case that
    already has an open ticket, it's redirected to append_to_ticket rather
    than filing a second one -- CLAUDE.md's "at most one ticket per case"
    now holds even for the model's own direct tool choice, not just the
    orchestrator's internal triggers."""
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()

    create_fn_1 = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-001", "simulate_failure": True})]
                )
            ),
            FakeResponse(FakeMessage(content="Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none.")),
        ]
    )
    run_turn(case_state, [], "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn_1)
    first_ticket_id = case_state.escalation.ticket_id

    turn2_draft = (
        "Verified: None.\nAssumed: None.\n"
        f"Simulated: New context was added to your existing ticket, {first_ticket_id}.\n"
        f"Next step: A human reviewer will follow up on ticket {first_ticket_id}."
    )
    create_fn_2 = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c2",
                            "escalate_to_human",
                            {"reason": "New detail to add", "case_summary": "APP-001 update"},
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=turn2_draft)),
        ]
    )
    run_turn(case_state, [], "One more thing about my case.", client=object(), model="fake-model", create_fn=create_fn_2)

    assert case_state.escalation.ticket_id == first_ticket_id  # same ticket, not a new one
    redirect_entry = next(t for t in case_state.tool_history if t.tool == "escalate_to_human" and t.args.get("reason") == "New detail to add")
    assert "appended to existing ticket" in redirect_entry.result_summary

    queue = json.loads((tmp_path / "escalation_queue.json").read_text(encoding="utf-8"))
    assert len(queue) == 1  # still exactly one ticket in the persisted store
    assert queue[0]["ticket_id"] == first_ticket_id
    assert len(queue[0]["updates"]) == 1
