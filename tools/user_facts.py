"""Tool: record_user_fact.

Records a fact the APPLICANT states in conversation -- a correction, a
change of circumstances, a constraint like being unable to obtain a
document -- as a typed, structural part of the case rather than letting it
live only in conversation text. The tool itself is a stateless pass-through
(it just confirms the key/value); the actual CaseState mutation (storing it
under user_reported_facts, detecting a conflict with an existing
tool-reported fact under the same key) happens in
agent.orchestrator._apply_result_to_case_state, matching how every other
tool's effect on CaseState is applied centrally.
"""

from __future__ import annotations

from schemas import RecordUserFactResult

RECORD_USER_FACT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "record_user_fact",
        "description": (
            "Record a fact the applicant just stated -- a correction, a change of circumstances, "
            "or a constraint (e.g. unable to obtain a required document) -- so it's tracked "
            "structurally instead of lost in conversation text. Call this whenever the applicant "
            "reports something new or different from what's on file, before re-running any checks "
            "that fact affects."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact_key": {
                    "type": "string",
                    "description": "Short snake_case key naming what this is about, e.g. 'transcript_attestation', 'stated_curriculum'.",
                },
                "fact_value": {
                    "type": "string",
                    "description": "What the applicant said, in your own words, e.g. 'Applicant cannot obtain an attested transcript through normal means.'",
                },
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of recording the fact.",
                    "default": False,
                },
            },
            "required": ["fact_key", "fact_value"],
        },
    },
}


def record_user_fact(fact_key: str, fact_value: str, simulate_failure: bool = False) -> RecordUserFactResult:
    if simulate_failure:
        return RecordUserFactResult(error="Simulated record-user-fact failure (simulate_failure=True).")
    return RecordUserFactResult(fact_key=fact_key, fact_value=fact_value)
