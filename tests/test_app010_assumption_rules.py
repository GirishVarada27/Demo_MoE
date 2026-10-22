"""APP-010 (Indian CBSE) used to escalate instead of answering.

Its only curriculum rule, curr-in-001, is an ASSUMPTION chunk (no official
CBSE threshold exists). The real trace showed the model filing that gap
under Verified, the validator rejecting the whole section with generic
text, and the case escalating after the rewrites ran out. These tests pin
the fix: only Verified claims need an OFFICIAL chunk, an Assumed claim
backed by curr-in-001 passes, the exact failing claim is fed back, and
the ordering gate requires get_applicant_record + evaluate_eligibility.
"""

from __future__ import annotations

from agent.orchestrator import run_turn
from schemas import CaseState
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create

_CBSE = "Indian (CBSE)"
_MISFILED_CLAIM = (
    "There are no official MOE pages specifying numeric equivalency requirements for Indian (CBSE) "
    "certificates (chunk curr-in-001)."
)
_MISFILED_DRAFT = (
    f"Verified:\n- {_MISFILED_CLAIM}\n"
    "Assumed: None.\n"
    "Simulated: Your mock file shows an attested grade 12 certificate and transcript.\n"
    "Next step: Submit your attested documents."
)
_ANSWER = (
    "Verified: None.\n"
    "Assumed: No official CBSE-specific numeric threshold has been published, per curr-in-001 -- "
    "based on assumed rules, verify with MoE. Your equivalency can't be confirmed against a CBSE "
    "threshold until MoE publishes one.\n"
    "Simulated: Your file in the mock case system has an attested grade 12 certificate and "
    "transcript, plus your passport and Emirates ID copies.\n"
    "Next step: Submit your attested documents through MoE's abroad (Grade 12) equivalency track; "
    "the CBSE threshold itself is unverified."
)


def _call(call_id: str, name: str, args: dict) -> FakeResponse:
    return FakeResponse(FakeMessage(tool_calls=[FakeToolCall(call_id, name, args)]))


def test_app010_answers_with_cbse_threshold_under_assumed_and_no_escalation(tmp_path, monkeypatch):
    queue_path = tmp_path / "escalation_queue.json"
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", queue_path)
    case_state = CaseState()
    captured: list[list[dict]] = []
    main_create = dispatching_create(
        [
            _call("c1", "check_documents", {"applicant_id": "APP-010", "curriculum": _CBSE}),
            # Too early: get_applicant_record and evaluate_eligibility haven't run -> ordering error.
            FakeResponse(FakeMessage(content=_ANSWER)),
            _call("c2", "get_applicant_record", {"applicant_id": "APP-010"}),
            _call("c3", "evaluate_eligibility", {"case_state": {"applicant_id": "APP-010", "curriculum": _CBSE}}),
            # The real trace's mistake: an ASSUMPTION chunk cited under Verified.
            FakeResponse(FakeMessage(content=_MISFILED_DRAFT)),
            FakeResponse(FakeMessage(content=_ANSWER)),
        ],
        # Nothing is presented as Verified in the final answer, so the verifier must never be asked.
        verifier_response=FakeResponse(FakeMessage(content='FAIL: "anything" -- verifier should not run')),
    )

    def create(**kwargs):
        captured.append(list(kwargs["messages"]))
        return main_create(**kwargs)

    result = run_turn(case_state, [], "My ID is APP-010. What's my equivalency status?", client=object(), model="fake-model", create_fn=create)

    assert result == _ANSWER
    assessed = result.split("Assumed:", 1)[1].split("Simulated:", 1)[0]
    assert "curr-in-001" in assessed and "CBSE" in assessed and "threshold" in assessed
    # No escalation anywhere.
    assert case_state.escalation is None
    assert not queue_path.exists()
    assert not any(t.tool == "escalate_to_human" for t in case_state.tool_history)

    validations = [t for t in case_state.tool_history if t.tool == "validate_draft"]
    assert validations[0].result_summary.startswith("rejected (ordering)")
    assert "get_applicant_record and evaluate_eligibility have not run this turn" in validations[0].result_summary
    # Exactly one real failure: the misfiled claim, quoted verbatim, with the fix named.
    failed = [t for t in validations if t.result_summary.startswith("FAILED")]
    assert len(failed) == 1
    rewrite_request = [m["content"] for m in captured[-1] if m["role"] == "user"][-1]
    assert f'"{_MISFILED_CLAIM}"' in rewrite_request
    assert "unconfirmed ASSUMPTION rule" in rewrite_request
    assert "Move a claim backed only by an ASSUMPTION chunk to Assumed" in rewrite_request
