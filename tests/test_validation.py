"""Tests for the pre-response validator: grounding checks, confidence gating, and escalation routing."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.validation import (
    check_eligibility_statements_grounded,
    check_required_sections,
    check_requirement_claims_grounded,
    llm_verify,
    validate_draft,
)
from schemas import CaseState, ToolCallTrace

VALID_DRAFT = "Verified: None yet.\nAssumed: None.\nSimulated: None.\nNext step: Please share the applicant's id."


def _trace(tool: str, result_summary: str, args: dict | None = None) -> ToolCallTrace:
    return ToolCallTrace(step=1, tool=tool, args=args or {}, result_summary=result_summary, timestamp=datetime.now(timezone.utc))


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


def _fake_create(content: str):
    def create(**kwargs):
        return _FakeResponse(content)

    return create


# --- check_required_sections -------------------------------------------------


def test_check_required_sections_all_present():
    assert check_required_sections(VALID_DRAFT) == []


def test_check_required_sections_missing_one():
    draft = "Verified: None.\nAssumed: None.\nNext step: Ask for more info."
    failures = check_required_sections(draft)
    assert len(failures) == 1
    assert "Simulated" in failures[0]


def test_check_required_sections_missing_all():
    failures = check_required_sections("Just a plain answer.")
    assert len(failures) == 4


# --- check_requirement_claims_grounded ---------------------------------------


def test_requirement_claims_grounded_skips_trivial_verified_section():
    assert check_requirement_claims_grounded(VALID_DRAFT, CaseState()) == []


def test_requirement_claims_grounded_fails_without_any_tool_history():
    draft = "Verified: Applicants must submit an attested transcript.\nAssumed: None.\nSimulated: None.\nNext step: Provide documents."
    failures = check_requirement_claims_grounded(draft, CaseState())
    assert failures
    assert "OFFICIAL" in failures[0]


def test_requirement_claims_grounded_passes_with_official_search_result():
    draft = "Verified: Attestation is required for foreign certificates, per attest-001.\nAssumed: None.\nSimulated: None.\nNext step: none."
    case_state = CaseState(tool_history=[_trace("search_requirements", "2 result(s), top=attest-001 [OFFICIAL] (score=0.9)")])
    assert check_requirement_claims_grounded(draft, case_state) == []


def test_requirement_claims_grounded_ignores_assumption_only_search_result():
    draft = "Verified: CBSE has a confirmed numeric threshold.\nAssumed: None.\nSimulated: None.\nNext step: none."
    case_state = CaseState(tool_history=[_trace("search_requirements", "1 result(s), top=curr-in-001 [ASSUMPTION] (score=0.8)")])
    failures = check_requirement_claims_grounded(draft, case_state)
    assert failures


def test_requirement_claims_grounded_rejects_check_documents_alone():
    """Point 6: check_documents operates on mock/synthetic applicant data, so
    even a successful result never grounds a VERIFIED claim on its own --
    that kind of finding belongs under Simulated instead."""
    draft = "Verified: The transcript is missing from the submitted documents.\nAssumed: None.\nSimulated: None.\nNext step: none."
    case_state = CaseState(tool_history=[_trace("check_documents", "present=[], missing=['transcript'], invalid=[]")])
    failures = check_requirement_claims_grounded(draft, case_state)
    assert failures
    assert "Simulated" in failures[0]


def test_requirement_claims_grounded_rejects_verified_statement_citing_a_tool_result():
    """Point 3 (testing APP-006): citing check_documents/evaluate_eligibility/
    get_applicant_record directly under Verified is rejected outright, even
    if an OFFICIAL chunk also happens to be in the session's tool history --
    a tool/mock result never grounds a Verified claim, period."""
    draft = (
        "Verified: Per check_documents, your transcript and certificate are attested and valid.\n"
        "Assumed: None.\nSimulated: None.\nNext step: none."
    )
    case_state = CaseState(
        tool_history=[
            _trace("search_requirements", "2 result(s), top=attest-001 [OFFICIAL] (score=0.9)"),
            _trace("check_documents", "present=['grade_12_certificate', 'transcript'], missing=[], invalid=[]"),
        ]
    )
    failures = check_requirement_claims_grounded(draft, case_state)
    assert failures
    assert "tool/mock result" in failures[0]
    assert "Simulated" in failures[0]


def test_requirement_claims_grounded_ignores_failed_check_documents():
    draft = "Verified: The transcript is missing from the submitted documents.\nAssumed: None.\nSimulated: None.\nNext step: none."
    case_state = CaseState(tool_history=[_trace("check_documents", "error: No applicant found with id 'APP-999'.")])
    failures = check_requirement_claims_grounded(draft, case_state)
    assert failures


# --- check_eligibility_statements_grounded -----------------------------------


def test_eligibility_statement_fails_without_evaluate_eligibility():
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none.\n\nThe applicant appears eligible."
    failures = check_eligibility_statements_grounded(draft, CaseState())
    assert failures


def test_eligibility_statement_passes_with_successful_evaluate_eligibility():
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none.\n\nThe applicant appears eligible under except-002."
    case_state = CaseState(tool_history=[_trace("evaluate_eligibility", "verdicts=[except-002[OFFICIAL]=PASS], undeterminable=[]")])
    assert check_eligibility_statements_grounded(draft, case_state) == []


def test_eligibility_statement_fails_with_failed_evaluate_eligibility():
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none.\n\nThe applicant appears eligible."
    case_state = CaseState(tool_history=[_trace("evaluate_eligibility", "error: No applicant found with id 'APP-999'.")])
    failures = check_eligibility_statements_grounded(draft, case_state)
    assert failures


def test_no_eligibility_language_skips_check_entirely():
    assert check_eligibility_statements_grounded(VALID_DRAFT, CaseState()) == []


# --- llm_verify ---------------------------------------------------------------


def test_llm_verify_pass():
    assert llm_verify("draft", CaseState(), _fake_create("PASS"), "model") == []


def test_llm_verify_fail_returns_reason():
    failures = llm_verify("draft", CaseState(), _fake_create("FAIL: states a fee not confirmed anywhere"), "model")
    assert failures == ["FAIL: states a fee not confirmed anywhere"]


def test_llm_verify_fail_with_no_reason_still_fails():
    failures = llm_verify("draft", CaseState(), _fake_create(""), "model")
    assert failures  # empty/falsy content is never treated as PASS


# --- validate_draft (combined) ------------------------------------------------


def test_validate_draft_passes_rule_based_and_llm_pass():
    result = validate_draft(VALID_DRAFT, CaseState(), create=_fake_create("PASS"), model="m")
    assert result.passed
    assert result.failures == []


def test_validate_draft_fails_on_missing_sections_without_calling_llm():
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return _FakeResponse("PASS")

    result = validate_draft("No structure here.", CaseState(), create=create, model="m")
    assert not result.passed
    assert len(result.failures) == 4
    assert calls["n"] == 0  # rule-based failure short-circuits before any LLM call


_GROUNDED_DRAFT = (
    "Verified: Attestation is required for foreign certificates, per attest-001.\n"
    "Assumed: None.\nSimulated: None.\nNext step: none."
)
_GROUNDED_STATE = CaseState(tool_history=[_trace("search_requirements", "1 result(s), top=attest-001 [OFFICIAL] (score=0.9)")])


def test_validate_draft_fails_when_llm_verifier_flags_claim():
    verdict = 'FAIL: "Attestation is required for foreign certificates, per attest-001." -- unsupported'
    result = validate_draft(_GROUNDED_DRAFT, _GROUNDED_STATE, create=_fake_create(verdict), model="m")
    assert not result.passed
    assert result.failures == [verdict]
    assert result.failing_claims[0].claim == "Attestation is required for foreign certificates, per attest-001."
    assert result.failing_claims[0].reason == "unsupported"


def test_validate_draft_skips_llm_verifier_when_nothing_is_presented_as_verified():
    result = validate_draft(VALID_DRAFT, CaseState(), create=_fake_create("FAIL: unsupported claim"), model="m")
    assert result.passed


def test_assumed_and_simulated_claims_pass_without_official_chunk():
    draft = (
        "Verified: None.\n"
        "Assumed: No official CBSE numeric threshold exists, per curr-in-001 (an assumption, verify with MoE).\n"
        "Simulated: Your mock file shows an attested certificate and transcript.\n"
        "Next step: none."
    )
    case_state = CaseState(tool_history=[_trace("evaluate_eligibility", "verdicts=[curr-in-001[ASSUMPTION]=UNDETERMINABLE], undeterminable=['curr-in-001']")])
    result = validate_draft(draft, case_state, create=_fake_create("FAIL: should never be asked"), model="m")
    assert result.passed


def test_verified_claim_citing_only_an_assumption_chunk_fails_with_exact_claim():
    claim = "There is no official CBSE threshold (chunk curr-in-001)."
    draft = f"Verified:\n- {claim}\nAssumed: None.\nSimulated: None.\nNext step: none."
    case_state = CaseState(tool_history=[_trace("evaluate_eligibility", "verdicts=[curr-in-001[ASSUMPTION]=UNDETERMINABLE], undeterminable=['curr-in-001']")])
    result = validate_draft(draft, case_state)
    assert not result.passed
    assert [f.claim for f in result.failing_claims] == [claim]
    assert "curr-in-001, an unconfirmed ASSUMPTION rule" in result.failing_claims[0].reason


def test_only_the_ungrounded_verified_claim_fails():
    grounded = "Attestation is required for foreign certificates, per attest-001."
    ungrounded = "The fee is AED 50."
    draft = f"Verified:\n- {grounded}\n- {ungrounded}\nAssumed: None.\nSimulated: None.\nNext step: none."
    result = validate_draft(draft, _GROUNDED_STATE)
    assert [f.claim for f in result.failing_claims] == [ungrounded]


def test_validate_draft_skips_llm_pass_when_create_not_provided():
    result = validate_draft(VALID_DRAFT, CaseState())
    assert result.passed
