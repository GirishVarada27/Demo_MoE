"""Tool: evaluate_eligibility.

Evaluates a case_state (applicant id + the curriculum to evaluate under)
rule-by-rule against the curriculum_requirements and exceptions knowledge
chunks relevant to that curriculum family. Every rule gets a verdict of
PASS, FAIL, or UNDETERMINABLE -- UNDETERMINABLE whenever the only grounding
is an ASSUMPTION chunk, the applicant's grades don't contain the fields
needed to evaluate a chunk's criteria, or (for except-003) the chunk's own
applicability depends on an unconfirmed threshold elsewhere. This never
fabricates a PASS/FAIL where the knowledge base can't actually support one
(CLAUDE.md rule 3) -- see docs/assumptions.md for why each ASSUMPTION
chunk is unconfirmed.
"""

from __future__ import annotations

from schemas import EligibilityCaseState, EvaluateEligibilityResult, RuleChunk, RuleEvaluation
from tools._curriculum import classify_curriculum_family
from tools.applicant_lookup import get_applicant_record
from tools.search_rules import load_rule_chunks

EVALUATE_ELIGIBILITY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "evaluate_eligibility",
        "description": "Evaluate an applicant's eligibility rule-by-rule against the curriculum-specific and exception knowledge chunks relevant to their curriculum. Returns a verdict (PASS/FAIL/UNDETERMINABLE) and reason per rule, plus which rules could not be determined.",
        "parameters": {
            "type": "object",
            "properties": {
                "case_state": {
                    "type": "object",
                    "description": "The applicant id and the curriculum to evaluate eligibility under.",
                    "properties": {
                        "applicant_id": {"type": "string"},
                        "curriculum": {"type": "string"},
                    },
                    "required": ["applicant_id", "curriculum"],
                },
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of evaluating eligibility.",
                    "default": False,
                },
            },
            "required": ["case_state"],
        },
    },
}


def _parse_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _evaluate_chunk(chunk: RuleChunk, grades_by_subject: dict[str, str]) -> tuple[str, str]:
    if chunk.label == "ASSUMPTION":
        return "UNDETERMINABLE", f"Chunk {chunk.id} is an unverified assumption, not an official rule; see docs/assumptions.md."

    if chunk.id == "except-002":
        msat_raw = grades_by_subject.get("msat math")
        ielts_raw = grades_by_subject.get("ielts")
        english_msat_raw = grades_by_subject.get("english msat")
        if msat_raw is None or (ielts_raw is None and english_msat_raw is None):
            return "UNDETERMINABLE", "Required MSAT Math and/or IELTS/English MSAT scores are not present in the applicant's subject grades."
        msat = _parse_float(msat_raw)
        if msat is None:
            return "UNDETERMINABLE", f"MSAT Math value '{msat_raw}' is not a parseable numeric score."
        ielts = _parse_float(ielts_raw) if ielts_raw is not None else None
        english_msat = _parse_float(english_msat_raw) if english_msat_raw is not None else None
        meets_english = (ielts is not None and ielts >= 5.0) or (english_msat is not None and english_msat >= 1100)
        if msat >= 500 and meets_english:
            return "PASS", "MSAT Math >= 500 and IELTS >= 5.0 or English MSAT >= 1100, per except-002."
        return "FAIL", "Does not meet the except-002 MSAT Math / IELTS / English MSAT thresholds."

    if chunk.id == "except-003":
        return "UNDETERMINABLE", "Applicability depends on the standard British-curriculum passing criteria (curr-uk-001), which has no confirmed official threshold."

    if chunk.id == "except-001":
        return "UNDETERMINABLE", "Whether this exception mechanism is still active for current applicants is unconfirmed; the source article scopes it to academic year 2017-2018 and earlier."

    if chunk.id == "curr-us-001":
        return "UNDETERMINABLE", "Confirms a baseline SAT Math/TOEFL requirement exists for American-curriculum applicants but does not state the passing threshold (see curr-us-002)."

    return "UNDETERMINABLE", f"Chunk {chunk.id} does not state a concrete, machine-checkable pass/fail threshold."


def evaluate_eligibility(case_state: EligibilityCaseState, simulate_failure: bool = False) -> EvaluateEligibilityResult:
    if simulate_failure:
        return EvaluateEligibilityResult(
            applicant_id=case_state.applicant_id,
            curriculum=case_state.curriculum,
            error="Simulated eligibility-evaluation failure (simulate_failure=True).",
        )

    lookup = get_applicant_record(case_state.applicant_id)
    if not lookup.found:
        return EvaluateEligibilityResult(applicant_id=case_state.applicant_id, curriculum=case_state.curriculum, error=lookup.error)

    grades_by_subject = {g.subject.lower(): g.grade for g in lookup.applicant.subject_grades}
    family = classify_curriculum_family(case_state.curriculum)

    all_chunks = load_rule_chunks(topics=["curriculum_requirements", "exceptions"])
    relevant = [c for c in all_chunks if family and classify_curriculum_family(c.text) == family]

    if any(c.id in ("except-002", "except-003") for c in relevant):
        except_001 = next((c for c in all_chunks if c.id == "except-001"), None)
        if except_001 and except_001 not in relevant:
            relevant.append(except_001)

    if not relevant:
        rule_results = [
            RuleEvaluation(
                chunk_id="NONE",
                topic="curriculum_requirements",
                label="NONE",
                verdict="UNDETERMINABLE",
                reason=(
                    f"No equivalency rule chunk found in the knowledge base matching curriculum "
                    f"'{case_state.curriculum}'; this is likely an unsupported country/curriculum "
                    "combination and should be escalated rather than guessed."
                ),
            )
        ]
    else:
        rule_results = []
        for chunk in relevant:
            verdict, reason = _evaluate_chunk(chunk, grades_by_subject)
            rule_results.append(RuleEvaluation(chunk_id=chunk.id, topic=chunk.topic, label=chunk.label, verdict=verdict, reason=reason))

    undeterminable_rule_ids = [r.chunk_id for r in rule_results if r.verdict == "UNDETERMINABLE"]
    return EvaluateEligibilityResult(
        applicant_id=case_state.applicant_id,
        curriculum=case_state.curriculum,
        rule_results=rule_results,
        undeterminable_rule_ids=undeterminable_rule_ids,
    )
