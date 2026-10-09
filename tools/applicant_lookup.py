"""Mock tool: get_applicant_record.

Loads synthetic applicant records from data/applicants.json. Stands in for
a real student-records system integration -- every record in that file is
labeled "SYNTHETIC" and results returned by this tool should be presented
to the user as SIMULATED (CLAUDE.md rule 2).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from schemas import ApplicantLookupResult, ApplicantRecord

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "applicants.json"

GET_APPLICANT_RECORD_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_applicant_record",
        "description": "Look up a synthetic applicant's record (country, curriculum, grades, submitted documents) by applicant id. Result is SIMULATED, not a real student-records lookup.",
        "parameters": {
            "type": "object",
            "properties": {
                "applicant_id": {"type": "string", "description": "The applicant's id, e.g. 'APP-001'."},
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of looking up the applicant.",
                    "default": False,
                },
            },
            "required": ["applicant_id"],
        },
    },
}


@lru_cache(maxsize=1)
def load_applicants() -> tuple[ApplicantRecord, ...]:
    """Read and validate every applicant record from data/applicants.json."""
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    return tuple(ApplicantRecord(**entry) for entry in raw)


def get_applicant_record(applicant_id: str, simulate_failure: bool = False) -> ApplicantLookupResult:
    """Look up a synthetic applicant by id.

    Returns a typed ApplicantLookupResult (CLAUDE.md rule 5) rather than
    raising on a missing id, so the agent/validator can decide the next
    step instead of crashing (CLAUDE.md rule 3).
    """
    if simulate_failure:
        return ApplicantLookupResult(
            found=False,
            error="Simulated lookup failure (simulate_failure=True).",
        )
    for applicant in load_applicants():
        if applicant.applicant_id == applicant_id:
            return ApplicantLookupResult(found=True, applicant=applicant)
    return ApplicantLookupResult(found=False, error=f"No applicant found with id '{applicant_id}'.")
