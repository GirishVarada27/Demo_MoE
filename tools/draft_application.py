"""Mock tool: create_draft_application.

Builds a SIMULATED draft application for an applicant and appends it to
the local data/draft_applications.json store, standing in for a real case-
management system draft-creation step. Never asserts a final decision --
only ever produces a draft (CLAUDE.md rule 2, rule 3).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from schemas import CreateDraftApplicationResult, DraftApplication
from tools.applicant_lookup import get_applicant_record

_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "draft_applications.json"

CREATE_DRAFT_APPLICATION_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_draft_application",
        "description": "Create a SIMULATED draft equivalency application for an applicant, recorded in the mock draft-applications store. Does not submit or decide anything.",
        "parameters": {
            "type": "object",
            "properties": {
                "applicant_id": {"type": "string", "description": "The applicant's id, e.g. 'APP-001'."},
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of creating a draft.",
                    "default": False,
                },
            },
            "required": ["applicant_id"],
        },
    },
}


def create_draft_application(applicant_id: str, simulate_failure: bool = False) -> CreateDraftApplicationResult:
    if simulate_failure:
        return CreateDraftApplicationResult(error="Simulated draft-creation failure (simulate_failure=True).")

    lookup = get_applicant_record(applicant_id)
    if not lookup.found:
        return CreateDraftApplicationResult(error=lookup.error)

    draft = DraftApplication(
        draft_id=f"DRAFT-{uuid4().hex[:8].upper()}",
        applicant_id=applicant_id,
        created_at=datetime.now(timezone.utc),
    )

    try:
        existing = json.loads(_STORE_PATH.read_text(encoding="utf-8")) if _STORE_PATH.exists() else []
        existing.append(json.loads(draft.model_dump_json()))
        _STORE_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return CreateDraftApplicationResult(error=f"Failed to write draft application to the mock store: {exc}")

    return CreateDraftApplicationResult(draft=draft)
