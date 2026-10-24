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
   - verified_claim_failures: each non-trivial claim in the "Verified"
     section must cite an OFFICIAL chunk id retrieved this session -- a
     mock-data tool result belongs under Simulated, an ASSUMPTION-chunk
     claim under Assumed. Claims already under Assumed/Simulated are never
     claim-checked and always pass. Each failure carries the exact claim.
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
   - check_missing_documents_requested: if check_documents found a required
     document missing (and the applicant hasn't said they can't get it),
     the draft must name it, say it blocks the application, and ask the
     applicant to supply it.
   If any rule-based check fails, the (costly) LLM verifier pass is
   skipped entirely -- there's no point double-checking a draft already
   known to be unsupported.

2. LLM verifier (only runs if rule-based checks all pass and something is
   presented as Verified): a second, independent model call that sees this
   session's tool outputs and retrieved chunk texts plus only the Verified
   claims, and flags the first one they don't support.
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
shown the actual tool outputs and the full text of every knowledge chunk retrieved during this session, \
and the claims someone else's draft presents as VERIFIED. Your only job is to check whether each claim \
is directly supported by the cited OFFICIAL chunk text or the tool outputs. Reply with exactly one line: \
either "PASS" if every claim is supported, or FAIL: "<the exact claim text, copied verbatim>" -- <reason> \
for the first unsupported claim you find. Do not answer the user's question yourself."""

_LLM_FAIL_PATTERN = re.compile(r'^FAIL:\s*"(?P<claim>.+?)"\s*(?:--|—|-|:)\s*(?P<reason>.*)$', re.DOTALL)


class ClaimFailure(BaseModel):
    """One exact claim from the draft that failed, and why -- shown verbatim
    to the model on a rewrite and to the user if the draft never passes."""

    claim: str
    reason: str


class ValidationResult(BaseModel):
    passed: bool
    failures: list[str] = []
    # The exact Verified claims behind `failures`, when the failure is about a claim
    # (empty for structural failures like a missing section).
    failing_claims: list[ClaimFailure] = []


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


_CHUNK_ID_PATTERN = re.compile(r"\b[a-z]+(?:-[a-z]+)*-\d{3}\b")
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def verified_claims(draft: str) -> list[str]:
    """The non-trivial claims in the draft's Verified section, one per line
    or bullet. Assumed/Simulated/Next step content is never returned -- only
    a claim presented as Verified needs an OFFICIAL chunk behind it."""
    verified_text = _extract_section(draft, "Verified")
    if not verified_text or _is_trivial_verified(verified_text):
        return []
    claims = [_BULLET_PREFIX.sub("", line).strip() for line in verified_text.splitlines()]
    return [c for c in claims if c and not _is_trivial_verified(c)]


def _cited_label(chunk_id: str, case_state: CaseState) -> str | None:
    chunk = case_state.retrieved_chunks.get(chunk_id)
    if chunk is not None:
        return chunk.label
    for entry in case_state.tool_history:
        if entry.tool in ("search_requirements", "evaluate_eligibility") and f"{chunk_id}[ASSUMPTION]" in entry.result_summary.replace(" [", "["):
            return "ASSUMPTION"
    return None


def verified_claim_failures(draft: str, case_state: CaseState) -> list[ClaimFailure]:
    """VERIFIED means grounded in an OFFICIAL knowledge chunk retrieved this
    session, cited by its chunk id -- checked claim by claim. A claim that
    cites a tool/mock result (check_documents, evaluate_eligibility, ...)
    belongs under Simulated; one that cites only an ASSUMPTION chunk
    belongs under Assumed. Claims already filed under Assumed or Simulated
    are never checked here and never fail."""
    official_ids = _official_chunk_ids_this_session(case_state)
    failures = []
    for claim in verified_claims(draft):
        if _TOOL_RESULT_CITATION_PATTERN.search(claim):
            failures.append(
                ClaimFailure(claim=claim, reason="it cites a tool/mock result, which belongs under Simulated, not Verified")
            )
            continue
        if any(chunk_id in claim for chunk_id in official_ids):
            continue
        assumption_ids = [cid for cid in _CHUNK_ID_PATTERN.findall(claim) if _cited_label(cid, case_state) == "ASSUMPTION"]
        if assumption_ids:
            reason = (
                f"it cites {', '.join(assumption_ids)}, an unconfirmed ASSUMPTION rule, not an official one "
                "-- it belongs under Assumed"
            )
        else:
            reason = (
                "it doesn't cite an OFFICIAL rule retrieved this session (e.g. 'per attest-001') -- an "
                "applicant-specific finding belongs under Simulated, an unconfirmed rule under Assumed"
            )
        failures.append(ClaimFailure(claim=claim, reason=reason))
    return failures


def _format_claim_failure(failure: ClaimFailure) -> str:
    return f"Verified claim \"{failure.claim}\" failed: {failure.reason}."


def check_requirement_claims_grounded(draft: str, case_state: CaseState) -> list[str]:
    """String form of verified_claim_failures, one failure per exact claim
    (the 'Simulated'/'OFFICIAL' wording is what the rewrite request relies on)."""
    return [_format_claim_failure(f) for f in verified_claim_failures(draft, case_state)]


def _succeeded(entry) -> bool:
    """A trace entry for a tool call that actually ran and succeeded -- not a
    failure, and not a call the orchestrator rejected before running it."""
    return not entry.result_summary.startswith(("error:", "rejected"))


def check_eligibility_statements_grounded(draft: str, case_state: CaseState) -> list[str]:
    if not _ELIGIBILITY_PATTERN.search(draft):
        return []
    has_successful_eligibility_check = any(
        entry.tool == "evaluate_eligibility" and _succeeded(entry) for entry in case_state.tool_history
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
    draft_created = any(entry.tool == "create_draft_application" and _succeeded(entry) for entry in case_state.tool_history)
    if not draft_created:
        return []

    eligibility_entries = [entry for entry in case_state.tool_history if entry.tool == "evaluate_eligibility" and _succeeded(entry)]
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


UNAVAILABLE_FACT_VALUES = {"unavailable", "no", "not available", "cannot obtain", "can't obtain", "none"}

_BLOCKING_PATTERN = re.compile(
    r"\b(block\w*|on hold|required before|(?:cannot|can't|can ?not) (?:be )?(?:proceed|processed|continue|move forward))\b",
    re.IGNORECASE,
)
_SUPPLY_REQUEST_PATTERN = re.compile(
    r"\b(please|could you|can you|kindly)\b.*?\b(provide|upload|submit|supply|send|share|attach)\b",
    re.IGNORECASE | re.DOTALL,
)


def _doc_label(doc_type: str) -> str:
    """'emirates_id_copy' -> 'emirates id', the way a person would name it."""
    return doc_type.removesuffix("_copy").replace("_", " ")


def supplyable_missing_documents(case_state: CaseState) -> list[str]:
    """Doc types check_documents found missing, minus any the applicant has
    reported they can't obtain (e.g. user_reported_facts {"emirates_id":
    "unavailable"}) -- those are a different problem, not something to ask
    them to upload."""
    if case_state.documents is None:
        return []
    unavailable_keys = [
        key for key, fact in case_state.user_reported_facts.items() if fact.value.strip().lower() in UNAVAILABLE_FACT_VALUES
    ]
    return [
        d
        for d in case_state.documents.missing
        if not any(_doc_label(d).replace(" ", "_") in key for key in unavailable_keys)
    ]


def check_missing_documents_requested(draft: str, case_state: CaseState) -> list[str]:
    """A missing document the applicant can supply is a blocking request to
    them, not a reason to escalate or to answer around it."""
    missing = supplyable_missing_documents(case_state)
    if not missing:
        return []
    lowered = draft.lower()
    failures = []
    unnamed = [d for d in missing if _doc_label(d) not in lowered and d not in lowered]
    if unnamed:
        failures.append(f"Missing document(s) not named in the draft: {', '.join(unnamed)} -- name each one explicitly.")
    if not _BLOCKING_PATTERN.search(draft):
        failures.append("The draft doesn't state that the missing document(s) block the application.")
    if not _SUPPLY_REQUEST_PATTERN.search(draft):
        failures.append("The draft doesn't ask the applicant to supply the missing document(s) (e.g. 'please upload ...').")
    return failures


def llm_verify(draft: str, case_state: CaseState, create: Callable[..., Any], model: str) -> list[str]:
    """`draft` is whatever text should be checked -- validate_draft passes
    only the Verified claims. The verifier sees every retrieved chunk's full
    text, not just the one-line tool summaries: a summary only names the top
    search hit, which made the verifier reject citations of chunks that
    really were retrieved (APP-010's elig-002)."""
    tool_outputs = "\n".join(f"- {entry.tool}({entry.args}) -> {entry.result_summary}" for entry in case_state.tool_history)
    chunks = "\n".join(f"- {c.id} [{c.label}]: {c.text}" for c in case_state.retrieved_chunks.values())
    messages = [
        {"role": "system", "content": LLM_VERIFIER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Tool outputs gathered this session:\n{tool_outputs or '(none)'}\n\n"
                f"Knowledge chunks retrieved this session:\n{chunks or '(none)'}\n\n"
                f"Claims presented as Verified:\n{draft}"
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
    """Only a claim presented as Verified can fail for lack of grounding;
    Assumed and Simulated content is never claim-checked (by the rule pass
    or the LLM verifier). The remaining checks are structural."""
    claim_failures = verified_claim_failures(draft, case_state)
    failures = check_required_sections(draft, case_state)
    failures += [_format_claim_failure(f) for f in claim_failures]
    failures += check_eligibility_statements_grounded(draft, case_state)
    failures += check_draft_application_caveated(draft, case_state)
    failures += check_existing_escalation_referenced(draft, case_state)
    failures += check_missing_documents_requested(draft, case_state)
    if failures:
        return ValidationResult(passed=False, failures=failures, failing_claims=claim_failures)

    claims = verified_claims(draft)
    if claims and create is not None and model is not None:
        llm_failures = llm_verify("\n".join(f"- {c}" for c in claims), case_state, create, model)
        if llm_failures:
            match = _LLM_FAIL_PATTERN.match(llm_failures[0])
            failing = (
                ClaimFailure(claim=match["claim"].strip(), reason=match["reason"].strip() or "the verifier found it unsupported")
                if match
                else ClaimFailure(claim=claims[0] if len(claims) == 1 else "; ".join(claims), reason=llm_failures[0])
            )
            return ValidationResult(passed=False, failures=llm_failures, failing_claims=[failing])

    return ValidationResult(passed=True, failures=[])
