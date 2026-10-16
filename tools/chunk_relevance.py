"""Tool: record_chunk_relevance.

Records the agent's own read on whether a knowledge chunk search_requirements
surfaced actually applies to the case at hand -- for example, when
evaluate_eligibility finds no curriculum-specific rule chunk by its coarse
keyword match, but search_requirements's embeddings search turns up a
possibly relevant chunk anyway (agent.orchestrator._handle_no_rule_found
flags this as an open question for the agent to resolve). Deciding whether
that chunk actually applies requires reading its text and reasoning about
it -- not something the orchestrator can determine deterministically -- so
the agent calls this tool with its decision instead of only discussing it
in prose, where it would be lost to a bare open_questions string.

The tool itself is a stateless pass-through, same pattern as
tools/user_facts.py::record_user_fact; the actual CaseState mutation
(storing it under case_state.chunk_relevance, clearing the matching
open_questions entry) happens in
agent.orchestrator._apply_result_to_case_state.
"""

from __future__ import annotations

from schemas import RecordChunkRelevanceResult

RECORD_CHUNK_RELEVANCE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "record_chunk_relevance",
        "description": (
            "Record your decision on whether a specific knowledge chunk search_requirements "
            "retrieved actually applies to this case -- call this after reading a chunk that was "
            "flagged as possibly relevant but not automatically matched (e.g. in an open question), "
            "instead of only discussing it in prose."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {"type": "string", "description": "The id of the chunk being assessed, e.g. 'elig-002'."},
                "decision": {
                    "type": "string",
                    "enum": ["applies", "does_not_apply", "partially_applies"],
                    "description": "Whether the chunk actually applies to this applicant's curriculum/country.",
                },
                "reason": {"type": "string", "description": "One line explaining the decision."},
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of recording the decision.",
                    "default": False,
                },
            },
            "required": ["chunk_id", "decision", "reason"],
        },
    },
}


def record_chunk_relevance(
    chunk_id: str,
    decision: str,
    reason: str,
    simulate_failure: bool = False,
) -> RecordChunkRelevanceResult:
    if simulate_failure:
        return RecordChunkRelevanceResult(error="Simulated record-chunk-relevance failure (simulate_failure=True).")
    return RecordChunkRelevanceResult(chunk_id=chunk_id, decision=decision, reason=reason)
