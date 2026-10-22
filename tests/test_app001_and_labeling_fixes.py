"""Regression tests for the second round of "testing with APP-001" fixes.

1. evaluate_eligibility no longer blanket-returns UNDETERMINABLE for every
   ASSUMPTION-labeled chunk. curr-us-002 now has corroborated secondary-
   sourced numeric thresholds (docs/assumptions.md) and computes a
   provisional PASS/FAIL from them when the applicant's scores are
   present -- APP-001 (complete American file, SAT Math 650, TOEFL 95)
   gets a PASS, not another UNDETERMINABLE.
2. At most one escalation ticket per case (agent/orchestrator.py's
   _escalate_once) -- a second escalation trigger reuses the existing
   ticket id instead of filing a new one.
3. "What documents do I need?" still calls only search_requirements (no
   regression from this round's prompt/validator changes).
4. _summarize_result's check_documents branch no longer claims a passport
   or Emirates ID "passed attestation" -- those doc types were never
   attestation-checked in the first place (point 8's contradiction bug).
5. The transcript change-of-circumstances scenario itself lives in
   tests/test_change_of_circumstances.py (updated this round for the
   stricter VERIFIED-grounding rule below) -- not duplicated here.
6. check_requirement_claims_grounded now requires an OFFICIAL chunk
   specifically; check_documents/evaluate_eligibility results alone never
   ground a VERIFIED claim (point 6) -- see tests/test_validation.py for
   the unit-level coverage; this file's APP-001 scenario exercises it
   end-to-end.
"""

from __future__ import annotations

from agent.orchestrator import _summarize_result, run_turn
from agent.validation import check_required_sections
from schemas import CaseState
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create
from tools.document_check import check_documents


def _tool_names(case_state: CaseState) -> list[str]:
    return [entry.tool for entry in case_state.tool_history]


# --- 1. APP-001: no escalation, exactly one provisional result ---------------


def test_app001_complete_file_gets_provisional_result_without_escalation():
    case_state = CaseState()
    draft = (
        "Verified: None.\n"
        "Assumed: Based on assumed rules (SAT Math >= 450 and TOEFL >= 61, secondary-sourced and "
        "not officially confirmed), your scores suggest you would meet the standard American-"
        "curriculum threshold -- verify with MoE for a binding determination.\n"
        "Simulated: Per the mock case file, all your documents (certificate, transcript, passport, "
        "Emirates ID) are present and valid; eligibility was evaluated against the mock record.\n"
        "Next step: This is a provisional read, not a final determination. Let me know if you'd "
        "like this escalated to a human reviewer for a binding decision."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-001"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c2", "check_documents", {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"}
                        )
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c3",
                            "evaluate_eligibility",
                            {"case_state": {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"}},
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(
        case_state, [], "Can you check my equivalency? My ID is APP-001.", client=object(), model="fake-model", create_fn=create_fn
    )

    assert result == draft  # validated on the first try -- no revision, no escalation
    tool_names = _tool_names(case_state)
    assert "escalate_to_human" not in tool_names
    assert "validate_draft" not in tool_names

    # Exactly one provisional (ASSUMPTION-grounded) PASS this session, the rest legitimately
    # undeterminable for distinct, honest reasons -- not a blanket "ASSUMPTION = undeterminable".
    eligibility_entry = next(t for t in case_state.tool_history if t.tool == "evaluate_eligibility")
    assert "curr-us-002[ASSUMPTION]=PASS" in eligibility_entry.result_summary
    assert "curr-us-001" in eligibility_entry.result_summary and "UNDETERMINABLE" in eligibility_entry.result_summary


# --- 2. At most one escalation ticket per case -------------------------------


def test_at_most_one_escalation_ticket_per_case_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()

    # Turn 1: a tool fails twice, forcing the first real escalation.
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
    first_ticket_id = case_state.facts["escalation_ticket_id"]

    # Turn 2: validation fails after both rewrites, triggering a second escalation for the same case.
    create_fn_2 = dispatching_create(
        [
            FakeResponse(FakeMessage(content="no structure here")),
            FakeResponse(FakeMessage(content="still no structure")),
            FakeResponse(FakeMessage(content="no structure a third time")),
        ]
    )
    run_turn(case_state, [], "hi again", client=object(), model="fake-model", create_fn=create_fn_2)
    second_ticket_id = case_state.facts["escalation_ticket_id"]

    assert first_ticket_id == second_ticket_id  # same ticket, not a new one
    escalate_entries = [t for t in case_state.tool_history if t.tool == "escalate_to_human"]
    assert len(escalate_entries) == 2
    assert "(appended to existing ticket)" in escalate_entries[1].result_summary
    assert "(appended to existing ticket)" not in escalate_entries[0].result_summary

    # The persisted ticket itself was actually updated, not just referenced in memory.
    import json

    queue = json.loads((tmp_path / "escalation_queue.json").read_text(encoding="utf-8"))
    assert len(queue) == 1  # still exactly one ticket in the store
    assert queue[0]["ticket_id"] == first_ticket_id
    assert len(queue[0]["updates"]) == 1
    assert "validation" in queue[0]["updates"][0].lower()


# --- 3. "What documents do I need?" still calls only search_requirements ----


def test_documents_question_still_calls_only_search_requirements(monkeypatch):
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    draft = (
        "Verified: Certificates issued abroad must be attested before or as part of equivalency "
        "processing, per attest-001.\n"
        "Assumed: Based on commonly required supporting documents (not confirmed as an exhaustive "
        "official list this session), you'll likely also need your grade 10-12 certificates, an "
        "Emirates ID copy, and a passport copy; foreign-language certificates typically need a "
        "certified translation.\n"
        "Simulated: None.\n"
        "Next step: Let me know if you'd like me to check these against a specific case."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[FakeToolCall("c1", "search_requirements", {"query": "What documents are required for certificate equivalency?"})]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, [], "What documents do I need?", client=object(), model="fake-model", create_fn=create_fn)

    assert _tool_names(case_state) == ["search_requirements"]
    assert check_required_sections(result) == []


# --- 4. check_documents summary never claims attestation for doc types that don't need it ---


def test_check_documents_summary_never_claims_passport_or_emirates_id_are_attested():
    result = check_documents("APP-001", "American (US High School Diploma)")
    summary = _summarize_result("check_documents", result)

    assert "passport_copy" in summary
    assert "emirates_id_copy" in summary
    # Attestation is only ever claimed for the doc types that are actually checked for it.
    attested_section, other_section = summary.split("present (no attestation required")
    assert "passport_copy" not in attested_section
    assert "emirates_id_copy" not in attested_section
    assert "grade_12_certificate" in attested_section
    assert "transcript" in attested_section
