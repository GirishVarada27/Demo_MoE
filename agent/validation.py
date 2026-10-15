"""Pre-response validation layer.

Runs on every draft answer before it reaches the user -- a draft is never
sent as-is; agent.orchestrator.run_turn either gets it revised once or
escalates (CLAUDE.md rules 2 and 3). Two passes:

1. Rule-based (cheap, no model call):
   - check_required_sections: the draft must contain all four required
     output sections (Verified, Assumed, Simulated, Next step), plus a
     fifth -- "What you told me" -- whenever this session actually has
     user-reported facts to summarize (point 6: user-stated facts get
     their own section, not folded into Verified/Assumed/Simulated).
   - check_requirement_claims_grounded: if the draft's "Verified" section
     states something non-trivial, an OFFICIAL-labeled knowledge chunk
     (via search_requirements or evaluate_eligibility) must actually have
     been retrieved this session -- a mock-data tool result alone
     (check_documents, or an ASSUMPTION-grounded evaluate_eligibility
     verdict) does NOT ground a Verified claim; that belongs under
     Simulated/Assumed instead.
   - check_eligibility_statements_grounded: if the draft makes an
     eligibility/status statement anywhere, a successful evaluate_eligibility
     call must exist this session.
   - check_draft_application_caveated: if create_draft_application was
     called, evaluate_eligibility must have reached a determinable PASS/FAIL
     this session, or the draft must caveat the application as provisional.
   If any rule-based check fails, the (costly) LLM verifier pass is
   skipped entirely -- there's no point double-checking a draft already
   known to be unsupported.

2. LLM verifier (only runs if rule-based checks all pass): a second,
   independent model call that only sees this session's actual tool
   outputs and the draft, and flags any claim they don't support.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel

from schemas import CaseState

REQUIRED_SECTIONS = ["Verified", "Assumed", "Simulated", "Next step"]
# "What you told me" is only required when the session actually has
# user-reported facts to summarize -- see check_required_sections -- but
# _extract_section still needs to know its name as a possible boundary
# whenever it DOES appear, so section extraction doesn't run past it.
KNOWN_SECTIONS = REQUIRED_SECTIONS + ["What you told me"]

_TRIVIAL_VERIFIED_PATTERN = re.compile(r"(?i)^(no\b|none\b|nothing\b|n/?a\b|not\b)")


def _is_trivial_verified(text: str) -> bool:
    """True if `text` makes no substantive claim (a hedge/negation like "None
    yet" or "No verified information is available yet"), so it needs no
    grounding. Matched by leading hedge word rather than an exact-phrase set,
    since a real model phrases "nothing to report" many different ways --
    an exact-match set was missing most of them and forced grounding on
    answers that were already honestly saying there's nothing to ground."""
    stripped = text.strip(" .-*•")
    return not stripped or bool(_TRIVIAL_VERIFIED_PATTERN.match(stripped))

_ELIGIBILITY_PATTERN = re.compile(
    r"\b(eligible|ineligible|equivalent to|equivalency (?:is|has)|meets? the requirements?|"
    r"does not meet|approved|the verdict)\b",
    re.IGNORECASE,
)

LLM_VERIFIER_SYSTEM_PROMPT = """You are a strict fact-checking verifier, not the assistant. You will be \
shown the actual tool outputs gathered during this session and a draft answer someone else wrote. Your \
only job is to check whether the draft states anything -- a fact, requirement, document status, or \
eligibility outcome -- that is NOT directly supported by the attached tool outputs. Reply with exactly \
one line: either "PASS" if every claim is supported, or "FAIL: <reason>" naming the first unsupported \
claim you find. Do not answer the user's question yourself."""


class ValidationResult(BaseModel):
    passed: bool
    failures: list[str] = []


def _extract_section(draft: str, header: str) -> str | None:
    other_headers = "|".join(h.replace(" ", r"\s+") for h in KNOWN_SECTIONS if h.lower() != header.lower())
    pattern = re.compile(
        rf"{header.replace(' ', r'\s+')}\s*:\s*(.*?)(?=\n\s*(?:{other_headers})\s*:|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(draft)
    return match.group(1).strip() if match else None


def check_required_sections(draft: str, case_state: CaseState | None = None) -> list[str]:
    lowered = draft.lower()
    required = list(REQUIRED_SECTIONS)
    if case_state is not None and case_state.user_reported_facts:
        # Point 6: user-stated facts get their own section, not folded into
        # Verified/Assumed/Simulated -- only required when there's actually
        # something the user told us to summarize this session.
        required.append("What you told me")
    return [f"Missing required section: '{section}'." for section in required if section.lower() not in lowered]


def check_requirement_claims_grounded(draft: str, case_state: CaseState) -> list[str]:
    """VERIFIED means grounded in an OFFICIAL knowledge chunk -- not just
    "some tool succeeded." check_documents/evaluate_eligibility operate on
    SYNTHETIC/mock applicant data, so an applicant-specific finding from
    them (e.g. "your transcript isn't attested") belongs under SIMULATED,
    not VERIFIED, even though the *rule* behind it (attestation is
    required) is OFFICIAL. Only an actual OFFICIAL-labeled chunk retrieved
    this session grounds a Verified claim."""
    verified_text = _extract_section(draft, "Verified")
    if not verified_text:
        return []
    if _is_trivial_verified(verified_text):
        return []

    has_official_chunk = any(
        entry.tool in ("search_requirements", "evaluate_eligibility") and "[OFFICIAL]" in entry.result_summary
        for entry in case_state.tool_history
    )
    if not has_official_chunk:
        return [
            f"The 'Verified' section claims '{verified_text}', but no OFFICIAL knowledge chunk was "
            "retrieved this session to ground it (a mock-data tool result like check_documents "
            "belongs under Simulated, not Verified)."
        ]
    return []


def check_eligibility_statements_grounded(draft: str, case_state: CaseState) -> list[str]:
    if not _ELIGIBILITY_PATTERN.search(draft):
        return []
    has_successful_eligibility_check = any(
        entry.tool == "evaluate_eligibility" and not entry.result_summary.startswith("error:")
        for entry in case_state.tool_history
    )
    if not has_successful_eligibility_check:
        return ["Draft makes an eligibility/status statement without a successful evaluate_eligibility result this session."]
    return []


_CAVEAT_PATTERN = re.compile(
    r"\b(caveat|not (?:yet )?confirmed|unconfirmed|undetermined|undeterminable|pending|"
    r"provisional|preliminary|subject to|not guaranteed|not final)\b",
    re.IGNORECASE,
)


def check_draft_application_caveated(draft: str, case_state: CaseState) -> list[str]:
    """If create_draft_application was called, eligibility must have actually
    reached PASS or FAIL this session -- otherwise the draft is only valid
    if it plainly caveats the application as provisional. Drafting an
    application and presenting it as settled when eligibility is still
    undetermined (or was never evaluated at all) is exactly the kind of
    overconfident claim CLAUDE.md rule 3 rules out."""
    draft_created = any(
        entry.tool == "create_draft_application" and not entry.result_summary.startswith("error:")
        for entry in case_state.tool_history
    )
    if not draft_created:
        return []

    eligibility_entries = [
        entry
        for entry in case_state.tool_history
        if entry.tool == "evaluate_eligibility" and not entry.result_summary.startswith("error:")
    ]
    determinable = bool(eligibility_entries) and (
        "=PASS" in eligibility_entries[-1].result_summary or "=FAIL" in eligibility_entries[-1].result_summary
    )
    if determinable:
        return []

    if not _CAVEAT_PATTERN.search(draft):
        return [
            "create_draft_application was called without evaluate_eligibility reaching a determinable "
            "PASS/FAIL this session, but the draft doesn't caveat the draft application as provisional."
        ]
    return []


def llm_verify(draft: str, case_state: CaseState, create: Callable[..., Any], model: str) -> list[str]:
    tool_outputs = "\n".join(f"- {entry.tool}({entry.args}) -> {entry.result_summary}" for entry in case_state.tool_history)
    messages = [
        {"role": "system", "content": LLM_VERIFIER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Tool outputs gathered this session:\n{tool_outputs or '(none)'}\n\nDraft answer to check:\n{draft}"
            ),
        },
    ]
    try:
        response = create(model=model, messages=messages)
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        # Fail safe: if the verifier call itself breaks, treat the draft as
        # unverified rather than letting the exception crash validation or
        # silently skipping the check (CLAUDE.md rule 3 -- never guess).
        return [f"LLM verifier call failed ({exc}); treating the draft as unverified."]
    if content.upper().startswith("PASS"):
        return []
    return [content or "LLM verifier flagged an unsupported claim but gave no reason."]


def validate_draft(
    draft: str,
    case_state: CaseState,
    create: Callable[..., Any] | None = None,
    model: str | None = None,
) -> ValidationResult:
    failures = check_required_sections(draft, case_state)
    failures += check_requirement_claims_grounded(draft, case_state)
    failures += check_eligibility_statements_grounded(draft, case_state)
    failures += check_draft_application_caveated(draft, case_state)
    if failures:
        return ValidationResult(passed=False, failures=failures)

    if create is not None and model is not None:
        llm_failures = llm_verify(draft, case_state, create, model)
        if llm_failures:
            return ValidationResult(passed=False, failures=llm_failures)

    return ValidationResult(passed=True, failures=[])
