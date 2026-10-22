"""APP-003 used to hit the tool-call limit and escalate instead of answering.

Its trace showed the model drafting an answer before evaluate_eligibility had
run (failing validation), recording on-file data as a "user-reported" fact,
and burning the iteration budget on both. These tests pin the fixes: the
validate-after-evaluate ordering gate, the record_user_fact gate, and a
missing document producing a request to the applicant rather than a ticket.
"""

from __future__ import annotations

from agent.orchestrator import run_turn
from schemas import CaseState
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create

_US = "American (US High School Diploma)"

_EMIRATES_ID_DRAFT = (
    "Verified: None.\n"
    "Assumed: None.\n"
    "Simulated: Your file in the mock case system has an attested grade 12 certificate, an attested "
    "transcript, and a passport copy, but your Emirates ID copy is missing.\n"
    "Next step: The missing Emirates ID copy blocks your application -- please upload a copy of your "
    "Emirates ID so it can proceed."
)


def _call(call_id: str, name: str, args: dict) -> FakeResponse:
    return FakeResponse(FakeMessage(tool_calls=[FakeToolCall(call_id, name, args)]))


def _evaluate(call_id: str) -> FakeResponse:
    return _call(call_id, "evaluate_eligibility", {"case_state": {"applicant_id": "APP-003", "curriculum": _US}})


def test_app003_asks_for_emirates_id_with_zero_escalations(tmp_path, monkeypatch):
    queue_path = tmp_path / "escalation_queue.json"
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", queue_path)
    case_state = CaseState()
    create_fn = dispatching_create(
        [
            _call("c1", "get_applicant_record", {"applicant_id": "APP-003"}),
            # The trace's failure: re-recording on-file data as a user fact.
            _call("c2", "record_user_fact", {"fact_key": "stated_curriculum", "fact_value": _US}),
            _call("c3", "check_documents", {"applicant_id": "APP-003", "curriculum": _US}),
            # Drafting before evaluate_eligibility -> ordering error, not a validation failure.
            FakeResponse(FakeMessage(content=_EMIRATES_ID_DRAFT)),
            # Trying to escalate over a document the applicant can supply -> declined.
            _call("c4", "escalate_to_human", {"reason": "Emirates ID missing", "case_summary": "APP-003"}),
            _evaluate("c5"),
            FakeResponse(FakeMessage(content=_EMIRATES_ID_DRAFT)),
        ]
    )

    result = run_turn(
        case_state, [], "Hi, my applicant ID is APP-003. Can you check my documents?", client=object(), model="fake-model", create_fn=create_fn
    )

    assert result == _EMIRATES_ID_DRAFT
    assert "emirates id" in result.lower() and "please upload" in result.lower()
    assert case_state.documents.missing == ["emirates_id_copy"]
    # Zero escalations: no ticket in state, none persisted, none queued in the trace.
    assert case_state.escalation is None
    assert case_state.facts.get("escalation_ticket_id") is None
    assert not queue_path.exists()
    assert not any(t.tool == "escalate_to_human" and "queued" in t.result_summary for t in case_state.tool_history)
    # The ordering rejection didn't count as a validation failure.
    assert not any(t.tool == "validate_draft" and t.result_summary.startswith("FAILED") for t in case_state.tool_history)
    assert case_state.user_reported_facts == {}


def test_missing_document_draft_that_doesnt_ask_for_it_fails_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    vague_draft = (
        "Verified: None.\nAssumed: None.\nSimulated: Your documents were checked in the mock case system.\n"
        "Next step: I'll keep reviewing your file."
    )
    create_fn = dispatching_create(
        [
            _call("c1", "check_documents", {"applicant_id": "APP-003", "curriculum": _US}),
            _call("c2", "get_applicant_record", {"applicant_id": "APP-003"}),
            _evaluate("c3"),
            FakeResponse(FakeMessage(content=vague_draft)),
            FakeResponse(FakeMessage(content=_EMIRATES_ID_DRAFT)),
        ]
    )

    result = run_turn(case_state, [], "Check my documents, APP-003", client=object(), model="fake-model", create_fn=create_fn)

    assert result == _EMIRATES_ID_DRAFT
    failed = [t for t in case_state.tool_history if t.tool == "validate_draft" and t.result_summary.startswith("FAILED")]
    assert len(failed) == 1
    assert "emirates_id_copy" in failed[0].result_summary
    assert "block the application" in failed[0].result_summary
    assert case_state.escalation is None


def test_greeting_causes_no_record_user_fact_call():
    case_state = CaseState()
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: Tell me your applicant ID or your question."
    create_fn = dispatching_create(
        [
            _call("c1", "record_user_fact", {"fact_key": "greeting", "fact_value": "User said hello"}),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
    assert case_state.user_reported_facts == {}
    assert [t.result_summary.startswith("rejected:") for t in case_state.tool_history if t.tool == "record_user_fact"] == [True]


def test_bare_applicant_id_never_records_a_user_fact():
    case_state = CaseState()
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: What would you like me to check?"
    create_fn = dispatching_create(
        [
            _call("c1", "record_user_fact", {"fact_key": "applicant_id", "fact_value": "APP-003"}),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, [], "My applicant ID is APP-003", client=object(), model="fake-model", create_fn=create_fn)

    assert case_state.user_reported_facts == {}


def test_validate_draft_before_evaluate_eligibility_returns_ordering_error():
    case_state = CaseState()
    draft = "Verified: None.\nAssumed: None.\nSimulated: Applicant lookup is SIMULATED.\nNext step: none."
    captured: list[list[dict]] = []
    main_create = dispatching_create(
        [
            _call("c1", "get_applicant_record", {"applicant_id": "APP-003"}),
            FakeResponse(FakeMessage(content=draft)),  # too early
            _evaluate("c2"),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    def create(**kwargs):
        captured.append(list(kwargs["messages"]))
        return main_create(**kwargs)

    result = run_turn(case_state, [], "Look up APP-003", client=object(), model="fake-model", create_fn=create)

    assert result == draft
    ordering = [t for t in case_state.tool_history if t.tool == "validate_draft"]
    assert len(ordering) == 1
    assert ordering[0].result_summary.startswith("rejected (ordering)")
    assert "evaluate_eligibility has not run this turn" in ordering[0].result_summary
    # The model was told to evaluate first, in the very next call after its early draft.
    feedback = [m["content"] for m in captured[2] if m["role"] == "user"][-1]
    assert "Call evaluate_eligibility for applicant APP-003" in feedback
    assert "not a validation failure" in feedback
    # Order in the trace: lookup, ordering rejection, then evaluation -- and no escalation.
    assert [t.tool for t in case_state.tool_history] == ["get_applicant_record", "validate_draft", "evaluate_eligibility"]
    assert case_state.escalation is None
