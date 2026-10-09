"""Pre-response validation layer.

Runs on every draft answer before it reaches the user -- a draft is never
sent as-is; agent.orchestrator.run_turn either gets it revised once or
escalates (CLAUDE.md rules 2 and 3). Two passes:

1. Rule-based (cheap, no model call):
   - check_required_sections: the draft must contain all four required
     output sections (Verified, Assumed, Simulated, Next step).
   - check_requirement_claims_grounded: if the draft's "Verified" section
     states something non-trivial, an OFFICIAL knowledge chunk (via
     search_requirements or evaluate_eligibility) or a successful
     check_documents call must actually have been retrieved this session.
   - check_eligibility_statements_grounded: if the draft makes an
     eligibility/status statement anywhere, a successful evaluate_eligibility
     call must exist this session.
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

_TRIVIAL_VERIFIED_VALUES = {"none", "none yet", "n/a", "na", "nothing", "nothing yet", "-"}

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
    other_headers = "|".join(h.replace(" ", r"\s+") for h in REQUIRED_SECTIONS if h.lower() != header.lower())
    pattern = re.compile(
        rf"{header.replace(' ', r'\s+')}\s*:\s*(.*?)(?=\n\s*(?:{other_headers})\s*:|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(draft)
    return match.group(1).strip() if match else None


def check_required_sections(draft: str) -> list[str]:
    lowered = draft.lower()
    return [f"Missing required section: '{section}'." for section in REQUIRED_SECTIONS if section.lower() not in lowered]


def check_requirement_claims_grounded(draft: str, case_state: CaseState) -> list[str]:
    verified_text = _extract_section(draft, "Verified")
    if not verified_text:
        return []
    if verified_text.strip(" .").lower() in _TRIVIAL_VERIFIED_VALUES:
        return []

    has_official_chunk = any(
        entry.tool in ("search_requirements", "evaluate_eligibility") and "[OFFICIAL]" in entry.result_summary
        for entry in case_state.tool_history
    ) or any(
        entry.tool == "check_documents" and not entry.result_summary.startswith("error:")
        for entry in case_state.tool_history
    )
    if not has_official_chunk:
        return [
            f"The 'Verified' section claims '{verified_text}', but no OFFICIAL knowledge chunk or "
            "successful document check was retrieved this session to ground it."
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
    failures = check_required_sections(draft)
    failures += check_requirement_claims_grounded(draft, case_state)
    failures += check_eligibility_statements_grounded(draft, case_state)
    if failures:
        return ValidationResult(passed=False, failures=failures)

    if create is not None and model is not None:
        llm_failures = llm_verify(draft, case_state, create, model)
        if llm_failures:
            return ValidationResult(passed=False, failures=llm_failures)

    return ValidationResult(passed=True, failures=[])
