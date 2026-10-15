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
from datetime import datetime, timezone
from typing import Any, Callable

import config
from agent.prompts import SYSTEM_PROMPT
from agent.validation import validate_draft
from schemas import CaseState, EligibilityCaseState, EscalateToHumanResult, EscalationTicket, ToolCallTrace, UserReportedFact
from tools import TOOL_FUNCTIONS, TOOL_SCHEMAS
from tools.document_check import ATTESTATION_REQUIRED_DOC_TYPES
from tools.escalation import escalate_to_human

MAX_ITERATIONS = 6
_CHAT_API_VERSION = "2024-02-01"


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


def _apply_result_to_case_state(case_state: CaseState, name: str, args: dict, result: Any) -> None:
    if getattr(result, "error", None):
        return

    if name == "get_applicant_record" and result.found:
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
        case_state.documents = result

    elif name == "evaluate_eligibility":
        inner = args.get("case_state", {}) or {}
        new_curriculum = inner.get("curriculum")
        if new_curriculum and new_curriculum != case_state.curriculum:
            case_state.curriculum = new_curriculum
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


def _escalate_once(case_state: CaseState, reason: str, case_summary: str) -> tuple[EscalateToHumanResult, bool]:
    """At most one escalation ticket per case -- if one already exists for
    this case, reuse it instead of filing a new one every time something
    escalates. Returns (result, reused)."""
    existing_ticket_id = case_state.facts.get("escalation_ticket_id")
    if existing_ticket_id:
        reused_ticket = EscalationTicket(
            ticket_id=existing_ticket_id,
            reason=reason,
            case_summary=case_summary,
            created_at=datetime.now(timezone.utc),
        )
        return EscalateToHumanResult(ticket=reused_ticket), True

    result = escalate_to_human(reason=reason, case_summary=case_summary)
    if result.ticket:
        # Record it here, not just via _apply_result_to_case_state -- that path
        # only runs when the MODEL calls escalate_to_human as a regular tool
        # choice. The orchestrator's own internal escalation triggers (tool
        # failed twice, validation failed twice, max iterations, model call
        # failed) call this function directly and need the ticket id recorded
        # too, or the "reuse" check above would never find it next time.
        case_state.facts["escalation_ticket_id"] = result.ticket.ticket_id
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
    """Call a tool, retrying once on failure. Logs every attempt as its own trace step."""
    for attempt in (1, 2):
        result, summary, succeeded = _call_tool_once(name, raw_args)
        _log_tool_call(case_state, name, raw_args, summary if attempt == 1 else f"{summary} (retry)")
        if succeeded:
            _apply_result_to_case_state(case_state, name, raw_args, result)
            return result, True
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
                _summarize_result("escalate_to_human", escalation) + (" (reused existing ticket)" if reused else ""),
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
            validation = validate_draft(draft, case_state, create=create, model=model)

            if validation.passed:
                turn_messages.append({"role": "assistant", "content": draft})
                conversation.extend(turn_messages)
                return draft

            if not revision_attempted:
                revision_attempted = True
                _log_validation_failure(case_state, attempt=1, draft=draft, failures=validation.failures)
                messages.append({"role": "assistant", "content": draft})
                turn_messages.append({"role": "assistant", "content": draft})
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
                _summarize_result("escalate_to_human", escalation) + (" (reused existing ticket)" if reused else ""),
            )
            ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
            fallback = (
                "I couldn't produce an answer that passed validation, so I've escalated this case to "
                f"a human reviewer (SIMULATED ticket {ticket_id})."
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
            if call_key in seen_calls:
                # Same tool, same args, already called this turn -- reuse the result instead
                # of invoking (and re-logging) it again.
                result_json = seen_calls[call_key]
                _log_tool_call(case_state, name, raw_args, "duplicate call this turn -- reused prior result, not re-invoked")
            else:
                result, succeeded = _execute_tool_with_retry(case_state, name, raw_args)

                if succeeded:
                    result_json = result.model_dump_json()
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
                        _summarize_result("escalate_to_human", escalation) + (" (reused existing ticket)" if reused else ""),
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
        _summarize_result("escalate_to_human", escalation) + (" (reused existing ticket)" if reused else ""),
    )
    ticket_id = escalation.ticket.ticket_id if escalation.ticket else "unavailable"
    fallback = (
        "I wasn't able to reach a validated answer within this session's tool-call limit, so "
        f"I've escalated this case to a human reviewer (SIMULATED ticket {ticket_id})."
    )
    turn_messages.append({"role": "assistant", "content": fallback})
    conversation.extend(turn_messages)
    return fallback
