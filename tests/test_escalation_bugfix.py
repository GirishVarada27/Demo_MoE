"""Regression tests for the "agent escalates on every message" bug report.

Root causes found and fixed:
1. agent/orchestrator.py::_summarize_result dropped the *reason* an invalid
   document failed (just doc_type) and didn't say "present" implied
   attestation/expiry/curriculum checks passed -- so the LLM verifier, which
   only sees result_summary (not the full tool JSON the model itself saw),
   flagged correct paraphrases ("the transcript is not attested", "an
   attested grade 12 certificate") as unsupported hallucinations.
2. agent/validation.py's trivial-"Verified"-value check was an exact-match
   set ({"none", "none yet", ...}) that missed realistic phrasing like
   "No verified information is available yet.", forcing OFFICIAL grounding
   onto answers that were already honestly saying there's nothing to ground.
3. evaluate_eligibility's UNDETERMINABLE verdicts for APP-009 are correct,
   not buggy (no official British-curriculum numeric threshold exists --
   see docs/assumptions.md) -- but agent/prompts.py's VALIDATE stage
   offered escalate_to_human as the go-to response to ANY UNDETERMINABLE
   rule, which structurally fires for every non-American curriculum.
4. agent/prompts.py's PLAN stage didn't explicitly separate a generic
   policy question ("what documents do I need") from an applicant-specific
   one, so the model tended to run the full lookup/check/evaluate sequence
   regardless of what was actually asked.
"""

from __future__ import annotations

from agent.orchestrator import run_turn
from agent.prompts import SYSTEM_PROMPT
from agent.validation import check_required_sections
from schemas import CaseState, EligibilityCaseState
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create
from tools.equivalency_calc import evaluate_eligibility


def _tool_names(case_state: CaseState) -> list[str]:
    return [entry.tool for entry in case_state.tool_history]


# --- 1. Trace panel shows the exact validator failure reason + the failing draft


def test_trace_shows_exact_validator_failure_reason_and_draft(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    bad_draft_1 = "No structure at all here."
    bad_draft_2 = "Still nothing structured."
    create_fn = dispatching_create(
        [FakeResponse(FakeMessage(content=bad_draft_1)), FakeResponse(FakeMessage(content=bad_draft_2))]
    )

    run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    validate_entries = [t for t in case_state.tool_history if t.tool == "validate_draft"]
    assert len(validate_entries) == 2
    assert "Missing required section: 'Verified'" in validate_entries[0].result_summary
    assert bad_draft_1 in validate_entries[0].result_summary
    assert bad_draft_2 in validate_entries[1].result_summary
    assert case_state.tool_history[-1].tool == "escalate_to_human"


# --- 2. evaluate_eligibility for APP-009: confirm it's correct, and the honest answer validates


def test_evaluate_eligibility_app009_verdicts_are_correct_not_buggy():
    """APP-009 (British GCE, strong grades, complete docs) comes back
    UNDETERMINABLE for every rule -- not a code bug, but because curr-uk-001
    (the only British-curriculum numeric-threshold chunk) is genuinely an
    unconfirmed ASSUMPTION (docs/assumptions.md). Documented here so it
    isn't "fixed" into a fabricated PASS by mistake later."""
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-009", curriculum="British (GCE)"))
    verdicts = {r.chunk_id: (r.label, r.verdict) for r in result.rule_results}
    assert verdicts["curr-uk-001"] == ("ASSUMPTION", "UNDETERMINABLE")
    assert verdicts["except-003"] == ("OFFICIAL", "UNDETERMINABLE")
    assert verdicts["except-001"] == ("OFFICIAL", "UNDETERMINABLE")


def test_app009_complete_file_with_undetermined_threshold_does_not_force_escalation():
    """The actual bug: an honest answer reporting complete documents plus an
    unconfirmed eligibility threshold used to fail validation (lost
    "not attested"-style reasons; too-narrow trivial-phrase matching), so
    the agent escalated on every such answer. Proves the fix: the same
    honest answer now validates cleanly on the first try."""
    case_state = CaseState()
    draft = (
        "Verified: Your submitted documents (certificate, transcript, passport, Emirates ID) are "
        "all present and have passed attestation, expiry, and curriculum checks.\n"
        "Assumed: None.\n"
        "Simulated: Applicant and document records were looked up from the mock case file.\n"
        "Next step: The official British-curriculum grade threshold isn't published, so a binding "
        "determination needs manual confirmation -- let me know if you'd like this escalated to a "
        "human reviewer for that."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-009"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c2", "check_documents", {"applicant_id": "APP-009", "curriculum": "British (GCE)"}
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
                            {"case_state": {"applicant_id": "APP-009", "curriculum": "British (GCE)"}},
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, [], "Check my file, ID APP-009.", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft  # validated and returned as-is -- no revision, no escalation
    tool_names = _tool_names(case_state)
    assert "escalate_to_human" not in tool_names
    assert "validate_draft" not in tool_names  # no failed attempt logged -- passed on the first try


# --- 3. Dynamic tool use: a generic policy question calls only search_requirements


def test_prompt_forces_no_tool_sequence_and_guides_generic_questions():
    assert "never follow a fixed, scripted tool sequence" in SYSTEM_PROMPT
    assert "needs search_requirements alone" in SYSTEM_PROMPT
    assert "do not call every tool" in SYSTEM_PROMPT
    # The old blanket "if undeterminable, escalate" instruction is gone --
    # replaced with explicit guidance that UNDETERMINABLE alone isn't a reason to.
    assert "not automatically a" in SYSTEM_PROMPT
    assert "reason to escalate" in SYSTEM_PROMPT


def test_documents_question_naming_an_applicant_still_calls_search_only(monkeypatch):
    """Mirrors the exact bug report: asking 'what documents do I need' while
    naming APP-009 must not cascade into looking up the applicant, checking
    documents, and evaluating eligibility -- a generic policy question stays
    generic even when a specific applicant is mentioned in passing."""
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    draft = (
        "Verified: You'll need your original grade 10-12 certificates, an Emirates ID copy, and a "
        "passport copy; foreign-language certificates also need a certified translation.\n"
        "Assumed: None.\nSimulated: None.\n"
        "Next step: Let me know if you'd like me to check these against your file, APP-009."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c1", "search_requirements", {"query": "What documents are required for certificate equivalency?"}
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(
        case_state, [], "What documents do I need? My ID is APP-009.", client=object(), model="fake-model", create_fn=create_fn
    )

    assert _tool_names(case_state) == ["search_requirements"]
    assert check_required_sections(result) == []
