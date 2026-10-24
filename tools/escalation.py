"""Mock tool: escalate_to_human.

Writes a SIMULATED escalation ticket to the local
data/escalation_queue.json queue, standing in for a real human case-
management system integration (CLAUDE.md rule 2).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from schemas import EscalateToHumanResult, EscalationTicket
from tools._data_dir import data_dir

_QUEUE_PATH = data_dir() / "escalation_queue.json"

ESCALATE_TO_HUMAN_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate_to_human",
        "description": "Escalate the current case to a human reviewer when the agent is unsure, a rule is undeterminable, or documents can't be resolved. Writes a SIMULATED ticket to the mock escalation queue.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why this case needs human review."},
                "case_summary": {"type": "string", "description": "Short summary of the case for the human reviewer."},
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of creating a ticket.",
                    "default": False,
                },
            },
            "required": ["reason", "case_summary"],
        },
    },
}


def escalate_to_human(reason: str, case_summary: str, simulate_failure: bool = False) -> EscalateToHumanResult:
    """Write a SIMULATED escalation ticket. Never raises -- this is the system's own

    safety net (agent.orchestrator falls back to it when a tool or validation fails
    repeatedly), so a failure here must degrade to a typed error, not an exception,
    or the one thing meant to always work becomes the thing that crashes the agent.
    """
    if simulate_failure:
        return EscalateToHumanResult(error="Simulated escalation failure (simulate_failure=True).")

    ticket = EscalationTicket(
        ticket_id=f"TCK-{uuid4().hex[:8].upper()}",
        reason=reason,
        case_summary=case_summary,
        created_at=datetime.now(timezone.utc),
    )

    try:
        existing = json.loads(_QUEUE_PATH.read_text(encoding="utf-8")) if _QUEUE_PATH.exists() else []
        existing.append(json.loads(ticket.model_dump_json()))
        _QUEUE_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return EscalateToHumanResult(error=f"Failed to write escalation ticket to the mock queue: {exc}")

    return EscalateToHumanResult(ticket=ticket)


def append_to_ticket(ticket_id: str, note: str) -> EscalateToHumanResult:
    """Append a new reason/context note to an EXISTING ticket instead of
    filing a new one -- at most one ticket per case (agent.orchestrator's
    _escalate_once), and a second escalation trigger should still carry its
    new context forward, not get silently discarded. Never raises -- same
    reasoning as escalate_to_human itself (this is also part of the
    safety-net path)."""
    try:
        existing = json.loads(_QUEUE_PATH.read_text(encoding="utf-8")) if _QUEUE_PATH.exists() else []
    except (OSError, json.JSONDecodeError) as exc:
        return EscalateToHumanResult(error=f"Failed to read the mock escalation queue: {exc}")

    for entry in existing:
        if entry.get("ticket_id") == ticket_id:
            entry.setdefault("updates", []).append(note)
            try:
                _QUEUE_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            except OSError as exc:
                return EscalateToHumanResult(error=f"Failed to write the mock escalation queue: {exc}")
            return EscalateToHumanResult(ticket=EscalationTicket(**entry))

    return EscalateToHumanResult(error=f"No existing ticket '{ticket_id}' found to append to.")
