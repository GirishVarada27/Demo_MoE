"""Main agent loop.

run_turn() is the single entry point: it calls Azure OpenAI with the
running message history, the CaseState summary, and the tool schemas; lets
the model decide whether and which tool(s) to call (CLAUDE.md rule 1 --
the agent decides, the tools execute); dispatches any requested tool
calls against real tool functions; folds each typed result back into the
session's CaseState; logs every tool call as a ToolCallTrace; and loops
until the model returns a final answer or MAX_ITERATIONS is reached, at
which point it escalates rather than let the model guess.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

import config
from agent.prompts import SYSTEM_PROMPT
from agent.validation import validate_draft
from schemas import (
    CaseState,
    CheckDocumentsResult,
    ChunkRelevanceNote,
    EligibilityCaseState,
    EscalateToHumanResult,
    EvaluateEligibilityResult,
    InvalidDocument,
    RuleEvaluation,
    ToolCallTrace,
    UserReportedFact,
)
from tools import TOOL_FUNCTIONS, TOOL_SCHEMAS
from tools._curriculum import classify_curriculum_family
from tools.document_check import ATTESTATION_REQUIRED_DOC_TYPES
from tools.escalation import append_to_ticket, escalate_to_human
from tools.search_rules import search_requirements

MAX_ITERATIONS = 6
_CHAT_API_VERSION = "2024-02-01"
_UNAVAILABLE_FACT_VALUES = {"unavailable", "no", "not available", "cannot obtain", "can't obtain", "none"}
_NO_RULE_SENTINEL_CHUNK_ID = "NONE"
# Tools whose mere invocation means this turn is about a specific applicant's
# case, not a generic policy question -- drives _is_informational_turn.
# escalate_to_human is included because calling it at all is itself a
# decision about a specific case; there's no such thing as escalating "a
# generic policy question" in the first place.
_APPLICANT_SPECIFIC_TOOLS = {
    "get_applicant_record",
    "check_documents",
    "evaluate_eligibility",
    "create_draft_application",
    "escalate_to_human",
}


def _unavailable_attested_doc_types(case_state: CaseState) -> list[str]:
    """Doc types the applicant has reported can't be attested, e.g. a
    user_reported_facts entry {"attested_transcript": "unavailable"} means
    the "transcript" doc type. Drives both overrides below."""
    doc_types = []
    for fact_key, fact in case_state.user_reported_facts.items():
        if fact_key.startswith("attested_") and fact.value.strip().lower() in _UNAVAILABLE_FACT_VALUES:
            doc_types.append(fact_key[len("attested_") :])
    return doc_types


def _apply_user_reported_document_override(case_state: CaseState, result: CheckDocumentsResult) -> CheckDocumentsResult:
    """If the applicant reports a document's attestation is unavailable,
    reflect that reality instead of a stale mock-data value that
    contradicts what they just said -- re-running check_documents is
    otherwise a no-op, since it's a pure function of (applicant_id,
    curriculum) alone and has no way to hear what the user just told us."""
    unavailable = set(_unavailable_attested_doc_types(case_state))
    if not unavailable.intersection(result.present):
        return result
    present = [d for d in result.present if d not in unavailable]
    invalid = list(result.invalid) + [
        InvalidDocument(doc_type=d, reason="applicant reports attestation is unavailable (user-reported)")
        for d in result.present
        if d in unavailable
    ]
    return CheckDocumentsResult(present=present, missing=result.missing, invalid=invalid, error=result.error)


def _apply_user_reported_eligibility_override(case_state: CaseState, result: EvaluateEligibilityResult) -> EvaluateEligibilityResult:
    """Same idea for evaluate_eligibility: a PASS computed from scores alone
    doesn't mean much if a required document can't actually be attested --
    downgrade it to UNDETERMINABLE rather than let the tool keep reporting
    a PASS the user's own words already contradict."""
    unavailable = _unavailable_attested_doc_types(case_state)
    if not unavailable:
        return result
    changed = False
    new_results = []
    for r in result.rule_results:
        if r.verdict != "PASS":
            new_results.append(r)
            continue
        changed = True
        new_results.append(
            RuleEvaluation(
                chunk_id=r.chunk_id,
                topic=r.topic,
                label=r.label,
                verdict="UNDETERMINABLE",
                reason=(
                    f"{r.reason} However, the applicant reports being unable to obtain attestation "
                    f"for: {', '.join(unavailable)}. A determination can't be confirmed without that "
                    "document, so this is UNDETERMINABLE rather than PASS until it's resolved."
                ),
            )
        )
    if not changed:
        return result
    return EvaluateEligibilityResult(
        applicant_id=result.applicant_id,
        curriculum=result.curriculum,
        rule_results=new_results,
        undeterminable_rule_ids=[r.chunk_id for r in new_results if r.verdict == "UNDETERMINABLE"],
        error=result.error,
    )


def _handle_no_rule_found(case_state: CaseState, result: EvaluateEligibilityResult) -> str | None:
    """evaluate_eligibility signals "no curriculum-specific rule chunk at all"
    with a single chunk_id='NONE' entry (tools/equivalency_calc.py), based on
    a coarse keyword family match. Before the agent is allowed to tell
    anyone a rule does or doesn't exist for their curriculum/country, this
    confirms that against search_requirements's embeddings search too
    (which might catch a chunk the keyword match missed) rather than
    trusting the keyword match alone -- and if confirmed, escalates
    immediately because of the missing rule itself, not because a later
    validation pass happens to fail on an unsupported claim. This is a
    narrow, conditional safety-net call, not a hardcoded per-turn tool
    sequence (CLAUDE.md rule 1 is about the latter) -- the same precedent
    already applies to _escalate_once's other triggers (tool failure,
    validation failure, max iterations), none of which wait for the model's
    say-so either.

    Returns a ready-to-send plain-language answer if this triggered an
    escalation (the caller should return it immediately, bypassing the
    model's own draft and validation), or None if there's nothing to
    short-circuit: not the no-rule case, already handled earlier this
    session, or search_requirements actually found something relevant
    after all (in which case a note is queued for the model instead).
    """
    if len(result.rule_results) != 1 or result.rule_results[0].chunk_id != _NO_RULE_SENTINEL_CHUNK_ID:
        return None
    if case_state.facts.get("no_rule_found_reason"):
        return None

    curriculum = case_state.curriculum or result.curriculum or "the stated curriculum"
    country = case_state.facts.get("country_of_origin") or "the stated country"
    family = classify_curriculum_family(curriculum)
    tag_curriculum = (family or curriculum).upper()

    query = f"equivalency rules for {curriculum} curriculum from {country}"
    search_result = search_requirements(query=query, curriculum=curriculum)
    _log_tool_call(
        case_state,
        "search_requirements",
        {"query": query, "curriculum": curriculum, "auto_triggered": "confirming no_rule_found"},
        _summarize_result("search_requirements", search_result),
    )

    if search_result.results:
        candidate_id = search_result.results[0].chunk.id
        note = (
            f"evaluate_eligibility found no curriculum-specific rule chunk for '{curriculum}', but "
            f"search_requirements found a possibly relevant chunk ({candidate_id}) -- read it and "
            "call record_chunk_relevance with your decision (applies/does_not_apply/"
            "partially_applies) before concluding no rule exists."
        )
        if note not in case_state.open_questions and candidate_id not in case_state.chunk_relevance:
            case_state.open_questions.append(note)
        return None

    reason_tag = f"no_rule_found:{tag_curriculum}/{country}"
    case_state.facts["no_rule_found_reason"] = reason_tag
    escalation, reused = _escalate_once(
        case_state,
        reason=f"{reason_tag} -- no equivalency rule chunk or indexed guidance exists for this curriculum/country combination.",
        case_summary=case_state.model_dump_json(),
    )
    _log_tool_call(
        case_state,
        "escalate_to_human",
        {"reason": "no_rule_found"},
        _summarize_result("escalate_to_human", escalation) + (" (appended to existing ticket)" if reused else ""),
    )
    ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
    return (
        f"I checked the official rules for {curriculum} from {country}, and there's no published "
        "equivalency rule for that combination yet, so I'm not able to confirm your certificate "
        f"equivalency automatically. I've escalated your case to a human reviewer (ticket {ticket_id}) "
        "who can assess it manually. In the meantime, let me know if there's anything else about "
        "your certificate or documents you'd like me to note for them."
    )


def _ensure_user_reported_facts_section(case_state: CaseState, draft: str) -> str:
    """The model is good at deciding whether a user-reported fact belongs in
    the answer, but not reliably good at remembering to format a "What you
    told me" section -- and a missing section used to fail validation over
    something the system can just supply deterministically. If this session
    has user-reported facts and the model's draft doesn't already include
    the section, append one built straight from CaseState (never the
    model's own words) instead of asking the model to get the formatting
    right. If there are no user-reported facts, nothing is added -- the
    section is simply absent, not a validation failure."""
    if not case_state.user_reported_facts:
        return draft
    if re.search(r"what\s+you\s+told\s+me", draft, re.IGNORECASE):
        return draft
    facts_text = "; ".join(f"{key}: {fact.value}" for key, fact in case_state.user_reported_facts.items())
    return draft.rstrip() + f"\n\nWhat you told me: {facts_text}."


_SECTION_FAILURE_PHRASES: list[tuple[str, str]] = [
    ("missing required section", "my draft answer was missing some required information"),
    ("official knowledge chunk", "a claim in my draft wasn't backed by an official rule I'd actually confirmed this session"),
    ("eligibility", "an eligibility statement in my draft wasn't backed by a completed eligibility check"),
    ("draft application", "a draft application wasn't clearly marked as provisional"),
]


def _plain_language_failure_reason(failures: list[str]) -> str:
    """Translate validator failure text into a citizen-facing phrase with no
    section names or validator wording -- the raw failures still go to the
    trace in full (_log_validation_failure); this is only for the chat
    reply, which must never quote them directly."""
    lowered = " ".join(failures).lower()
    for needle, phrase in _SECTION_FAILURE_PHRASES:
        if needle in lowered:
            return phrase
    return "part of my draft answer wasn't fully supported by what I'd actually verified this session"


def _is_informational_turn(case_state: CaseState, turn_start_index: int) -> bool:
    """True if this turn is a generic policy question, not about a specific
    applicant's case -- at least one REAL tool was called this turn, and
    none of them were applicant-specific. Restricted to TOOL_FUNCTIONS
    names (not "validate_draft"/"escalate_to_human" trace entries the
    orchestrator itself logs, which aren't tool choices the model made) --
    otherwise a second validation-failure pass would see its own prior
    "validate_draft" log entry and misread an answer-less turn (e.g. a bare
    unstructured reply to "hi") as "informational"."""
    turn_tools = {t.tool for t in case_state.tool_history[turn_start_index:] if t.tool in TOOL_FUNCTIONS}
    return bool(turn_tools) and not (turn_tools & _APPLICANT_SPECIFIC_TOOLS)


def _build_informational_fallback(case_state: CaseState, failures: list[str]) -> str:
    """For a generic policy question whose draft still failed validation
    after one revision: never escalate (there's no applicant case to
    escalate), just answer plainly from the chunks actually retrieved this
    session, say what couldn't be confirmed, and offer -- without creating
    a ticket -- to escalate if the applicant wants a human to look at it."""
    official_chunks = [c for c in case_state.retrieved_chunks.values() if c.label == "OFFICIAL"]
    if official_chunks:
        summary = " ".join(f"{c.text}" for c in official_chunks[:3])
        intro = f"Here's what the official rules say: {summary}"
    else:
        intro = "I didn't find anything in the official rules directly relevant to your question."
    return (
        f"{intro} I wasn't able to fully confirm a complete answer from that alone -- "
        f"{_plain_language_failure_reason(failures)}. Let me know if you'd like me to escalate "
        "this question to a human reviewer for a definitive answer, or if you can tell me more "
        "about your specific situation."
    )


def _build_client(settings: config.Settings | None = None):
    """Build the chat client: AzureOpenAI if AZURE_OPENAI_ENDPOINT is set, else
    plain OpenAI using OPENAI_API_KEY (CLAUDE.md: keep model-client choice
    configurable without touching any other orchestration code)."""
    settings = settings or config.get_settings()
    if settings.azure_openai_endpoint:
        from openai import AzureOpenAI

        return AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=_CHAT_API_VERSION,
        )

    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key.get_secret_value())


def _invoke_tool(name: str, raw_args: dict) -> Any:
    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool '{name}'")
    if name == "evaluate_eligibility":
        args = dict(raw_args)
        args["case_state"] = EligibilityCaseState(**args["case_state"])
        return TOOL_FUNCTIONS[name](**args)
    return TOOL_FUNCTIONS[name](**raw_args)


def _summarize_result(name: str, result: Any) -> str:
    error = getattr(result, "error", None)
    if error:
        return f"error: {error}"

    if name == "search_requirements":
        if not result.results:
            return "0 results"
        top = result.results[0]
        return f"{len(result.results)} result(s), top={top.chunk.id} [{top.chunk.label}] (score={top.score})"
    if name == "get_applicant_record":
        return f"found {result.applicant.applicant_id} ({result.applicant.name})" if result.found else "not found"
    if name == "check_documents":
        invalid_desc = [f"{d.doc_type} ({d.reason})" for d in result.invalid]
        attested_present = [d for d in result.present if d in ATTESTATION_REQUIRED_DOC_TYPES]
        other_present = [d for d in result.present if d not in ATTESTATION_REQUIRED_DOC_TYPES]
        return (
            f"present and attested (attestation/expiry/curriculum checks passed)={attested_present}, "
            f"present (no attestation required for this doc type; expiry/curriculum checks passed)={other_present}, "
            f"missing={result.missing}, invalid={invalid_desc}"
        )
    if name == "evaluate_eligibility":
        verdicts = ", ".join(f"{r.chunk_id}[{r.label}]={r.verdict}" for r in result.rule_results)
        return f"verdicts=[{verdicts}], undeterminable={result.undeterminable_rule_ids}"
    if name == "create_draft_application":
        return f"draft {result.draft.draft_id} created (SIMULATED)"
    if name == "escalate_to_human":
        return f"ticket {result.ticket.ticket_id} queued (SIMULATED)"
    if name == "record_user_fact":
        return f"recorded user-reported fact '{result.fact_key}': {result.fact_value}"
    return str(result)[:200]


def _apply_result_to_case_state(case_state: CaseState, name: str, args: dict, result: Any) -> Any:
    """Fold a tool result into CaseState. Returns the result to actually use
    going forward (the model's tool message and case_state storage both use
    this return value) -- normally just `result` unchanged, but
    check_documents/evaluate_eligibility may come back overridden to
    reflect a user-reported fact the raw tool call can't see on its own."""
    if getattr(result, "error", None):
        return result

    if name == "search_requirements":
        for match in result.results:
            case_state.retrieved_chunks.setdefault(match.chunk.id, match.chunk)

    elif name == "get_applicant_record" and result.found:
        applicant = result.applicant
        case_state.applicant_id = applicant.applicant_id
        case_state.facts.setdefault("name", applicant.name)
        case_state.facts.setdefault("country_of_origin", applicant.country_of_origin)
        case_state.facts["stated_curriculum_on_file"] = applicant.stated_curriculum
        if case_state.curriculum is None:
            case_state.curriculum = applicant.stated_curriculum
            note = (
                f"Assumed curriculum '{applicant.stated_curriculum}' from the applicant's "
                "on-file record; confirm with the applicant if this has changed."
            )
            if note not in case_state.assumptions:
                case_state.assumptions.append(note)
        elif case_state.curriculum != applicant.stated_curriculum:
            note = (
                f"On-file stated curriculum ('{applicant.stated_curriculum}') differs from the "
                f"curriculum currently being evaluated ('{case_state.curriculum}')."
            )
            if note not in case_state.open_questions:
                case_state.open_questions.append(note)

    elif name == "check_documents":
        new_curriculum = args.get("curriculum")
        if new_curriculum and new_curriculum != case_state.curriculum:
            case_state.curriculum = new_curriculum
        result = _apply_user_reported_document_override(case_state, result)
        case_state.documents = result

    elif name == "evaluate_eligibility":
        inner = args.get("case_state", {}) or {}
        new_curriculum = inner.get("curriculum")
        if new_curriculum and new_curriculum != case_state.curriculum:
            case_state.curriculum = new_curriculum
        result = _apply_user_reported_eligibility_override(case_state, result)
        verdicts = ", ".join(f"{r.chunk_id}={r.verdict}" for r in result.rule_results)
        case_state.facts["last_eligibility_check"] = verdicts
        if result.undeterminable_rule_ids:
            note = f"Eligibility undeterminable for rule(s): {', '.join(result.undeterminable_rule_ids)}."
            if note not in case_state.open_questions:
                case_state.open_questions.append(note)

    elif name == "create_draft_application":
        case_state.facts["draft_id"] = result.draft.draft_id

    elif name == "escalate_to_human":
        case_state.facts["escalation_ticket_id"] = result.ticket.ticket_id
        case_state.escalation = result.ticket

    elif name == "record_chunk_relevance":
        case_state.chunk_relevance[result.chunk_id] = ChunkRelevanceNote(
            chunk_id=result.chunk_id, decision=result.decision, reason=result.reason
        )
        case_state.open_questions = [
            q for q in case_state.open_questions if result.chunk_id not in q
        ]

    elif name == "record_user_fact":
        fact_key, fact_value = result.fact_key, result.fact_value
        case_state.user_reported_facts[fact_key] = UserReportedFact(
            value=fact_value, recorded_at=datetime.now(timezone.utc)
        )
        existing = case_state.facts.get(fact_key)
        if existing is not None and existing != fact_value:
            note = (
                f"User reports: '{fact_value}' (as '{fact_key}'), which conflicts with the "
                f"on-file fact '{existing}'. Treating the user-reported fact as current."
            )
        else:
            # Not a strict key collision, but still something that changes the
            # picture -- surface it structurally rather than only in conversation
            # text, so re-planning actually accounts for it.
            note = (
                f"User-reported: '{fact_value}' (as '{fact_key}'). Re-verify any related "
                "checks against this before answering."
            )
        if note not in case_state.open_questions:
            case_state.open_questions.append(note)

    return result


def _escalate_once(case_state: CaseState, reason: str, case_summary: str) -> tuple[EscalateToHumanResult, bool]:
    """At most one escalation ticket per case -- if one already exists for
    this case, append the new reason as a note on it instead of filing a
    new ticket every time something escalates. Returns (result, reused)."""
    existing_ticket_id = case_state.facts.get("escalation_ticket_id")
    if existing_ticket_id:
        result = append_to_ticket(existing_ticket_id, note=reason)
        if result.ticket:
            case_state.escalation = result.ticket
            return result, True
        # The existing ticket id is stale/unreadable (e.g. a wiped mock
        # store) -- fall through and file a fresh one rather than lose the
        # escalation entirely.

    result = escalate_to_human(reason=reason, case_summary=case_summary)
    if result.ticket:
        # Record it here, not just via _apply_result_to_case_state -- that path
        # only runs when the MODEL calls escalate_to_human as a regular tool
        # choice. The orchestrator's own internal escalation triggers (tool
        # failed twice, validation failed twice, max iterations, model call
        # failed) call this function directly and need the ticket id (and
        # full escalation state -- CaseState.escalation, injected into the
        # model's context every turn) recorded too, or the "reuse" check
        # above would never find it next time.
        case_state.facts["escalation_ticket_id"] = result.ticket.ticket_id
        case_state.escalation = result.ticket
    return result, False


def _log_validation_failure(case_state: CaseState, attempt: int, draft: str, failures: list[str]) -> None:
    """Log a failed validation attempt to the trace, with the exact failure
    reason(s) and the draft that failed -- so the trace panel (and anyone
    diagnosing an escalation) can see *why*, not just that it happened."""
    _log_tool_call(
        case_state,
        "validate_draft",
        {"attempt": attempt},
        f"FAILED: {'; '.join(failures)} | draft: {draft}",
    )


def _log_tool_call(case_state: CaseState, name: str, args: dict, result_summary: str) -> None:
    case_state.tool_history.append(
        ToolCallTrace(
            step=len(case_state.tool_history) + 1,
            tool=name,
            args=args,
            result_summary=result_summary,
            timestamp=datetime.now(timezone.utc),
        )
    )


def _call_tool_once(name: str, raw_args: dict) -> tuple[Any | None, str, bool]:
    try:
        result = _invoke_tool(name, raw_args)
    except Exception as exc:  # unknown tool name or bad args from the model
        return None, f"tool invocation error: {exc}", False
    summary = _summarize_result(name, result)
    succeeded = not summary.startswith("error:")
    return (result if succeeded else None), summary, succeeded


def _execute_tool_with_retry(case_state: CaseState, name: str, raw_args: dict) -> tuple[Any | None, bool]:
    """Call a tool, retrying once on failure. Logs every attempt as its own
    trace step -- after applying any user-reported-fact override, so the
    trace (and the result sent back to the model) shows the corrected
    picture, not a stale one that contradicts what the user just said."""
    for attempt in (1, 2):
        result, summary, succeeded = _call_tool_once(name, raw_args)
        if succeeded:
            result = _apply_result_to_case_state(case_state, name, raw_args, result)
            summary = _summarize_result(name, result)
            _log_tool_call(case_state, name, raw_args, summary if attempt == 1 else f"{summary} (retry)")
            return result, True
        _log_tool_call(case_state, name, raw_args, summary if attempt == 1 else f"{summary} (retry)")
    return None, False


def _call_model_with_retry(create: Callable[..., Any], model: str, messages: list[dict]) -> tuple[Any | None, str | None]:
    """Call the chat model, retrying once on failure. Returns (response, error)."""
    last_error = None
    for _ in range(2):
        try:
            return create(model=model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto"), None
        except Exception as exc:
            last_error = str(exc)
    return None, last_error


def run_turn(
    case_state: CaseState,
    conversation: list[dict],
    user_message: str,
    client: Any | None = None,
    model: str | None = None,
    create_fn: Callable[..., Any] | None = None,
    inject_failures: bool = False,
) -> str:
    """Run one user turn through the tool-calling loop.

    Mutates case_state and conversation in place (both are the caller's
    persisted session state) and returns the final answer text. client and
    model are injectable so tests never need real Azure credentials.
    inject_failures forces simulate_failure=True on every tool call this
    turn, regardless of what the model requested -- a demo/test knob (the
    UI's "inject tool failures" toggle) for exercising the retry-then-
    escalate path on demand.
    """
    if client is None or model is None:
        settings = config.get_settings()
        client = client or _build_client(settings)
        model = model or (settings.azure_openai_deployment if settings.azure_openai_endpoint else settings.openai_model)
    create = create_fn or client.chat.completions.create

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": (
                "Current case state (for your own reasoning; do not dump this verbatim to the "
                f"user):\n{case_state.model_dump_json(indent=2)}"
            ),
        },
        *conversation,
        {"role": "user", "content": user_message},
    ]
    turn_messages: list[dict] = [{"role": "user", "content": user_message}]
    revision_attempted = False
    turn_start_index = len(case_state.tool_history)
    seen_calls: dict[tuple[str, str], str] = {}  # (tool, canonical args) -> result JSON already returned this turn

    for _ in range(MAX_ITERATIONS):
        response, call_error = _call_model_with_retry(create, model, messages)
        if response is None:
            escalation, reused = _escalate_once(
                case_state,
                reason=f"Azure OpenAI chat completion call failed twice: {call_error}",
                case_summary=case_state.model_dump_json(),
            )
            _log_tool_call(
                case_state,
                "escalate_to_human",
                {"reason": "model_call_failed"},
                _summarize_result("escalate_to_human", escalation) + (" (appended to existing ticket)" if reused else ""),
            )
            ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
            fallback = (
                "I'm having trouble reaching the model service right now, so I've escalated this "
                f"case to a human reviewer (SIMULATED ticket {ticket_id}). Please try again shortly."
            )
            turn_messages.append({"role": "assistant", "content": fallback})
            conversation.extend(turn_messages)
            return fallback

        message = response.choices[0].message

        if not message.tool_calls:
            draft = message.content or ""
            draft = _ensure_user_reported_facts_section(case_state, draft)
            validation = validate_draft(draft, case_state, create=create, model=model)

            if validation.passed:
                turn_messages.append({"role": "assistant", "content": draft})
                conversation.extend(turn_messages)
                return draft

            informational = _is_informational_turn(case_state, turn_start_index)

            if not revision_attempted:
                revision_attempted = True
                _log_validation_failure(case_state, attempt=1, draft=draft, failures=validation.failures)
                messages.append({"role": "assistant", "content": draft})
                turn_messages.append({"role": "assistant", "content": draft})
                if informational:
                    revision_request = (
                        "Your draft answer failed validation for the following reason(s):\n"
                        + "\n".join(f"- {f}" for f in validation.failures)
                        + "\nThis is a general policy question, not about a specific applicant's case -- "
                        "revise your answer using only the text of the knowledge chunks search_requirements "
                        "actually retrieved this session, and cite their chunk id(s) under Verified. Do not "
                        "call escalate_to_human for a question like this; if something still can't be "
                        "confirmed from those chunks, say so plainly instead."
                    )
                else:
                    revision_request = (
                        "Your draft answer failed validation for the following reason(s):\n"
                        + "\n".join(f"- {f}" for f in validation.failures)
                        + "\nRevise your answer to address every issue above. Include all four required "
                        "sections (Verified, Assumed, Simulated, Next step), and make sure every "
                        "eligibility/status statement follows a successful evaluate_eligibility call."
                    )
                messages.append({"role": "user", "content": revision_request})
                turn_messages.append({"role": "user", "content": revision_request})
                continue

            _log_validation_failure(case_state, attempt=2, draft=draft, failures=validation.failures)

            if informational:
                # A generic policy question has no applicant case to escalate -- never file a
                # ticket for one; answer from the retrieved chunks and offer escalation in words only.
                fallback = _build_informational_fallback(case_state, validation.failures)
                turn_messages.append({"role": "assistant", "content": fallback})
                conversation.extend(turn_messages)
                return fallback

            escalation, reused = _escalate_once(
                case_state,
                reason=(
                    "Draft answer failed validation twice: "
                    + "; ".join(validation.failures)
                    + f" | final draft: {draft}"
                ),
                case_summary=case_state.model_dump_json(),
            )
            _log_tool_call(
                case_state,
                "escalate_to_human",
                {"reason": "validation_failed_twice"},
                _summarize_result("escalate_to_human", escalation) + (" (appended to existing ticket)" if reused else ""),
            )
            ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
            fallback = (
                "I wasn't able to confirm this answer carefully enough to send it as final -- "
                f"{_plain_language_failure_reason(validation.failures)}. I've escalated your case to "
                f"a human reviewer (ticket {ticket_id}) so they can confirm the details directly. "
                "Let me know if there's anything else you'd like to add in the meantime."
            )
            turn_messages.append({"role": "assistant", "content": fallback})
            conversation.extend(turn_messages)
            return fallback

        assistant_msg = {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ],
        }
        messages.append(assistant_msg)
        turn_messages.append(assistant_msg)

        for tc in message.tool_calls:
            name = tc.function.name
            try:
                raw_args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                raw_args = {}
            if inject_failures:
                raw_args["simulate_failure"] = True

            call_key = (name, json.dumps(raw_args, sort_keys=True))

            if (
                name == "escalate_to_human"
                and case_state.escalation is None
                and case_state.applicant_id is None
                and _is_informational_turn(case_state, turn_start_index)
            ):
                # Informational question, no applicant case on file -- there's nothing to
                # escalate. Decline instead of filing a ticket for a generic policy question.
                result_json = json.dumps(
                    {
                        "declined": True,
                        "reason": (
                            "This looks like a general informational question, not tied to a "
                            "specific applicant case -- answer directly from the retrieved "
                            "knowledge chunks instead of escalating."
                        ),
                    }
                )
                _log_tool_call(case_state, name, raw_args, "declined: informational question, no ticket created")
                seen_calls[call_key] = result_json
                tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result_json}
                messages.append(tool_msg)
                turn_messages.append(tool_msg)
                continue

            if name == "escalate_to_human" and case_state.escalation is not None:
                # At most one ticket per case -- redirect a second escalate_to_human call (the
                # model's own choice, not one of the orchestrator's internal triggers already
                # routed through _escalate_once) to append to the existing ticket instead of
                # creating a new one.
                append_result = append_to_ticket(
                    case_state.escalation.ticket_id, note=raw_args.get("reason", "Escalated again by the agent.")
                )
                if append_result.ticket:
                    case_state.escalation = append_result.ticket
                result_json = append_result.model_dump_json()
                _log_tool_call(
                    case_state,
                    name,
                    raw_args,
                    _summarize_result("escalate_to_human", append_result) + " (appended to existing ticket -- not a new one)",
                )
                seen_calls[call_key] = result_json
                tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result_json}
                messages.append(tool_msg)
                turn_messages.append(tool_msg)
                continue

            if call_key in seen_calls:
                # Same tool, same args, already called this turn -- reuse the result instead
                # of invoking (and re-logging) it again.
                result_json = seen_calls[call_key]
                _log_tool_call(case_state, name, raw_args, "duplicate call this turn -- reused prior result, not re-invoked")
            else:
                result, succeeded = _execute_tool_with_retry(case_state, name, raw_args)

                if succeeded:
                    result_json = result.model_dump_json()
                    if name == "evaluate_eligibility":
                        no_rule_answer = _handle_no_rule_found(case_state, result)
                        if no_rule_answer is not None:
                            seen_calls[call_key] = result_json
                            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result_json}
                            messages.append(tool_msg)
                            turn_messages.append(tool_msg)
                            turn_messages.append({"role": "assistant", "content": no_rule_answer})
                            conversation.extend(turn_messages)
                            return no_rule_answer
                else:
                    last_summary = case_state.tool_history[-1].result_summary
                    escalation, reused = _escalate_once(
                        case_state,
                        reason=f"Tool '{name}' failed twice: {last_summary}",
                        case_summary=case_state.model_dump_json(),
                    )
                    _log_tool_call(
                        case_state,
                        "escalate_to_human",
                        {"reason": f"tool_failure:{name}"},
                        _summarize_result("escalate_to_human", escalation) + (" (appended to existing ticket)" if reused else ""),
                    )
                    ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
                    result_json = json.dumps({"error": last_summary, "escalated": True, "ticket_id": ticket_id})

                seen_calls[call_key] = result_json

            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result_json}
            messages.append(tool_msg)
            turn_messages.append(tool_msg)

    escalation, reused = _escalate_once(
        case_state,
        reason="Agent reached the maximum tool-call iterations without producing a validated answer.",
        case_summary=case_state.model_dump_json(),
    )
    _log_tool_call(
        case_state,
        "escalate_to_human",
        {"reason": "max_iterations_reached"},
        _summarize_result("escalate_to_human", escalation) + (" (appended to existing ticket)" if reused else ""),
    )
    ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
    fallback = (
        "I wasn't able to reach a validated answer within this session's tool-call limit, so "
        f"I've escalated this case to a human reviewer (SIMULATED ticket {ticket_id})."
    )
    turn_messages.append({"role": "assistant", "content": fallback})
    conversation.extend(turn_messages)
    return fallback
