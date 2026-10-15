"""Regression tests for: "Actually, I can't get my attested transcript."
said after a provisional eligibility result still failed validation and
opened a new ticket every time.

Root cause (confirmed by reproducing it first): record_user_fact and a
re-run of check_documents/evaluate_eligibility were already wired up, but
re-running those tools was a no-op -- both are pure functions of
(applicant_id, curriculum) against the static mock data file, with no way
to hear what the user just said. So the agent was structurally stuck:
any honest attempt to say "this changes things" contradicted the tool
output it had just received, and the LLM verifier correctly flagged that
contradiction every time.

Fix: agent/orchestrator.py's _apply_user_reported_document_override and
_apply_user_reported_eligibility_override actually reflect a user-reported
"attested_<doc_type>: unavailable" fact in the tool results themselves --
check_documents moves the doc type to invalid, and evaluate_eligibility
downgrades any PASS verdict to UNDETERMINABLE with a reason citing the
user-reported constraint. Escalation now appends to the existing ticket
(tools/escalation.py::append_to_ticket) instead of filing a new one, and
the generic "I couldn't produce an answer" fallback was replaced with the
actual failure reasons in plain language.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent.orchestrator import (
    _apply_user_reported_document_override,
    _apply_user_reported_eligibility_override,
    run_turn,
)
from schemas import CaseState, CheckDocumentsResult, EvaluateEligibilityResult, RuleEvaluation, UserReportedFact
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create


def _tool_names(case_state: CaseState, start: int = 0) -> list[str]:
    return [entry.tool for entry in case_state.tool_history[start:]]


# --- unit coverage for the two override helpers -------------------------------


def test_document_override_flags_unavailable_attested_doc():
    case_state = CaseState(
        user_reported_facts={"attested_transcript": UserReportedFact(value="unavailable", recorded_at=datetime.now(timezone.utc))}
    )
    result = CheckDocumentsResult(present=["grade_12_certificate", "transcript"], missing=[], invalid=[])

    overridden = _apply_user_reported_document_override(case_state, result)

    assert overridden.present == ["grade_12_certificate"]
    assert len(overridden.invalid) == 1
    assert overridden.invalid[0].doc_type == "transcript"
    assert "user-reported" in overridden.invalid[0].reason


def test_document_override_is_a_noop_without_a_matching_fact():
    case_state = CaseState()
    result = CheckDocumentsResult(present=["grade_12_certificate", "transcript"], missing=[], invalid=[])
    assert _apply_user_reported_document_override(case_state, result) is result


def test_eligibility_override_downgrades_pass_to_undeterminable():
    case_state = CaseState(
        user_reported_facts={"attested_transcript": UserReportedFact(value="unavailable", recorded_at=datetime.now(timezone.utc))}
    )
    result = EvaluateEligibilityResult(
        applicant_id="APP-001",
        curriculum="American (US High School Diploma)",
        rule_results=[RuleEvaluation(chunk_id="curr-us-002", topic="curriculum_requirements", label="ASSUMPTION", verdict="PASS", reason="Provisional PASS, based on assumed rules.")],
        undeterminable_rule_ids=[],
    )

    overridden = _apply_user_reported_eligibility_override(case_state, result)

    assert overridden.rule_results[0].verdict == "UNDETERMINABLE"
    assert "transcript" in overridden.rule_results[0].reason
    assert overridden.undeterminable_rule_ids == ["curr-us-002"]


def test_eligibility_override_is_a_noop_without_a_matching_fact():
    case_state = CaseState()
    result = EvaluateEligibilityResult(
        applicant_id="APP-001",
        curriculum="American (US High School Diploma)",
        rule_results=[RuleEvaluation(chunk_id="curr-us-002", topic="curriculum_requirements", label="ASSUMPTION", verdict="PASS", reason="Provisional PASS.")],
        undeterminable_rule_ids=[],
    )
    assert _apply_user_reported_eligibility_override(case_state, result) is result


# --- the real scenario, end to end --------------------------------------------


def test_transcript_unavailable_after_provisional_result_changes_plan_without_new_ticket(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    conversation: list[dict] = []

    # Turn 1: APP-001's complete file gets a provisional PASS (prior session's fix).
    turn1_draft = (
        "Verified: None.\n"
        "Assumed: Based on assumed rules (SAT Math >= 450 and TOEFL >= 61, secondary-sourced and "
        "not officially confirmed), your scores suggest you would meet the standard American-"
        "curriculum threshold -- verify with MoE for a binding determination.\n"
        "Simulated: Per the mock case file, all your documents (certificate, transcript, passport, "
        "Emirates ID) are present and valid; eligibility was evaluated against the mock record.\n"
        "Next step: This is a provisional read, not a final determination. Let me know if you'd "
        "like this escalated to a human reviewer for a binding decision."
    )
    create_fn_1 = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-001"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall("c2", "check_documents", {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"})
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c3", "evaluate_eligibility", {"case_state": {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"}}
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=turn1_draft)),
        ]
    )
    run_turn(case_state, conversation, "Can you check my equivalency? My ID is APP-001.", client=object(), model="fake-model", create_fn=create_fn_1)

    turn1_tools = _tool_names(case_state)
    steps_after_turn1 = len(case_state.tool_history)
    assert case_state.facts.get("escalation_ticket_id") is None  # no ticket yet

    # Turn 2: the change of circumstances.
    turn2_draft = (
        "Verified: None.\nAssumed: None.\n"
        "Simulated: Per the mock case file, your transcript is now flagged as invalid since you "
        "report being unable to obtain its attestation; your other documents (certificate, "
        "passport, Emirates ID) remain present and valid. Your provisional eligibility result is "
        "now undetermined as a result -- it depended on a complete, attested file.\n"
        "What you told me: You said you cannot get your transcript attested.\n"
        "Next step: Options: check whether an alternative attestation route exists, I can "
        "prepare a provisional draft application clearly marked incomplete pending this "
        "document, or escalate to a human reviewer if you'd like a binding decision now -- let "
        "me know which you'd prefer."
    )
    create_fn_2 = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[FakeToolCall("c4", "record_user_fact", {"fact_key": "attested_transcript", "fact_value": "unavailable"})]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall("c5", "check_documents", {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"})
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c6", "evaluate_eligibility", {"case_state": {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"}}
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=turn2_draft)),
        ]
    )
    result = run_turn(
        case_state, conversation, "Actually, I can't get my attested transcript.", client=object(), model="fake-model", create_fn=create_fn_2
    )

    # 1. CaseState updated: the fact is recorded, and check_documents' stored result now
    #    reflects it (not the stale "all fine" picture from turn 1).
    assert case_state.user_reported_facts["attested_transcript"].value == "unavailable"
    assert case_state.user_reported_facts["attested_transcript"].label == "user_reported"
    assert any(d.doc_type == "transcript" for d in case_state.documents.invalid)
    assert "transcript" not in case_state.documents.present

    # 2. Eligibility was re-run, and its result actually changed (PASS -> UNDETERMINABLE).
    turn2_eligibility = next(t for t in case_state.tool_history[steps_after_turn1:] if t.tool == "evaluate_eligibility")
    assert "curr-us-002[ASSUMPTION]=UNDETERMINABLE" in turn2_eligibility.result_summary
    assert "curr-us-002[ASSUMPTION]=PASS" not in turn2_eligibility.result_summary

    # 3. The plan differs from the previous turn -- turn 2 doesn't repeat turn 1's
    #    get_applicant_record (already known) and leads with record_user_fact instead.
    turn2_tools = _tool_names(case_state, start=steps_after_turn1)
    assert turn2_tools == ["record_user_fact", "check_documents", "evaluate_eligibility"]
    assert turn2_tools != turn1_tools

    # 4. No new ticket -- the agent offered options instead of escalating.
    assert "escalate_to_human" not in turn2_tools
    assert case_state.facts.get("escalation_ticket_id") is None
    assert "validate_draft" not in turn2_tools  # passed validation on the first try

    assert result == turn2_draft
