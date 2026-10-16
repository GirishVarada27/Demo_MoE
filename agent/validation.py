"""Pre-response validation layer.

Runs on every draft answer before it reaches the user -- a draft is never
sent as-is; agent.orchestrator.run_turn either gets it revised once or
escalates (CLAUDE.md rules 2 and 3). Two passes:

1. Rule-based (cheap, no model call):
   - check_required_sections: the draft must contain the four required
     output sections (Verified, Assumed, Simulated, Next step). A fifth,
     optional section -- "What you told me" -- summarizes user-reported
     facts when this session has any, but its presence is guaranteed by
     agent.orchestrator._ensure_user_reported_facts_section (appended
     deterministically from CaseState before validation ever runs, not
     left to the model), so validation never fails a draft just for
     omitting it.
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
   - check_existing_escalation_referenced: if CaseState.escalation is set
     (an open ticket from a prior trigger this session), any mention of
     escalation in the draft must cite that ticket id, not read as a
     proposal to escalate again or file a new one.
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
    """case_state is accepted for API consistency with the other checks but
    unused here -- "What you told me" is never a required section (see the
    module docstring): its presence is guaranteed deterministically by the
    orchestrator before validation runs, not enforced by failing the draft."""
    lowered = draft.lower()
    return [f"Missing required section: '{section}'." for section in REQUIRED_SECTIONS if section.lower() not in lowered]


_TOOL_RESULT_CITATION_PATTERN = re.compile(
    r"\b(check_documents|evaluate_eligibility|get_applicant_record|create_draft_application|"
    r"record_user_fact|the mock (?:case file|record|queue|store)|per your (?:file|record))\b",
    re.IGNORECASE,
)

_OFFICIAL_CHUNK_ID_IN_SUMMARY_PATTERN = re.compile(r"([\w-]+)\s*\[OFFICIAL\]")


def _official_chunk_ids_this_session(case_state: CaseState) -> set[str]:
    """Chunk ids this session actually has an OFFICIAL-labeled hit for --
    from CaseState.retrieved_chunks (populated by
    agent.orchestrator._apply_result_to_case_state's search_requirements
    branch) and, for sessions/tests that only populate tool_history
    directly, the same ids as they appear in search_requirements/
    evaluate_eligibility trace summaries. A Verified claim must cite one of
    these ids by name, not just point at "some official chunk existed
    somewhere this session"."""
    ids = {cid for cid, chunk in case_state.retrieved_chunks.items() if chunk.label == "OFFICIAL"}
    for entry in case_state.tool_history:
        if entry.tool in ("search_requirements", "evaluate_eligibility"):
            ids.update(_OFFICIAL_CHUNK_ID_IN_SUMMARY_PATTERN.findall(entry.result_summary))
    return ids


def check_requirement_claims_grounded(draft: str, case_state: CaseState) -> list[str]:
    """VERIFIED means grounded in an OFFICIAL knowledge chunk, cited by its
    chunk id -- not just "some tool succeeded" or a vague appeal to "the
    official knowledge base." check_documents/evaluate_eligibility operate
    on SYNTHETIC/mock applicant data, so an applicant-specific finding from
    them (e.g. "your transcript isn't attested") belongs under SIMULATED,
    not VERIFIED, even though the *rule* behind it (attestation is
    required) is OFFICIAL -- and citing the tool/mock result directly is
    rejected outright, regardless of what else was retrieved this session."""
    verified_text = _extract_section(draft, "Verified")
    if not verified_text:
        return []
    if _is_trivial_verified(verified_text):
        return []

    if _TOOL_RESULT_CITATION_PATTERN.search(verified_text):
        return [
            f"The 'Verified' section claims '{verified_text}', but it cites a tool/mock result "
            "directly -- an applicant-specific finding from check_documents, evaluate_eligibility, "
            "get_applicant_record, or similar belongs under Simulated, not Verified, even when the "
            "underlying rule is official."
        ]

    official_ids = _official_chunk_ids_this_session(case_state)
    if not any(chunk_id in verified_text for chunk_id in official_ids):
        return [
            f"The 'Verified' section claims '{verified_text}', but doesn't cite an OFFICIAL "
            "knowledge chunk id retrieved this session (e.g. 'per attest-001') -- a general "
            "appeal to the official knowledge base, or a mock-data tool result like "
            "check_documents, doesn't ground a Verified claim on its own; that belongs under "
            "Simulated or Assumed instead."
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


_ESCALATION_MENTION_PATTERN = re.compile(r"\bescalat\w*\b", re.IGNORECASE)


def check_existing_escalation_referenced(draft: str, case_state: CaseState) -> list[str]:
    """Once a case has an open ticket (CaseState.escalation, injected into
    the model's context every turn), any mention of escalation in the
    answer must reference that existing ticket id, not read as a proposal
    to escalate again or open a new one."""
    if case_state.escalation is None:
        return []
    if not _ESCALATION_MENTION_PATTERN.search(draft):
        return []
    if case_state.escalation.ticket_id in draft:
        return []
    return [
        f"This case already has an open escalation ticket ({case_state.escalation.ticket_id}), but the "
        "draft discusses escalation without referencing that existing ticket -- it must reference the "
        "existing ticket, not propose escalating again or opening a new one."
    ]


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
    failures += check_existing_escalation_referenced(draft, case_state)
    if failures:
        return ValidationResult(passed=False, failures=failures)

    if create is not None and model is not None:
        llm_failures = llm_verify(draft, case_state, create, model)
        if llm_failures:
            return ValidationResult(passed=False, failures=llm_failures)

    return ValidationResult(passed=True, failures=[])
