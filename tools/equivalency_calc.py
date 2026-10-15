"""Tool: evaluate_eligibility.

Evaluates a case_state (applicant id + the curriculum to evaluate under)
rule-by-rule against the curriculum_requirements and exceptions knowledge
chunks relevant to that curriculum family. Every rule gets a verdict of
PASS, FAIL, or UNDETERMINABLE. UNDETERMINABLE is reserved for when a chunk
truly has no rule to apply (e.g. except-001's temporal-scope question, or
an ASSUMPTION chunk with no computable criteria) or the applicant's grades
genuinely lack the input data a chunk needs (e.g. except-002 without any
MSAT/IELTS score) -- not just because a chunk happens to be ASSUMPTION-
labeled. curr-us-002 is the one ASSUMPTION chunk with actual (secondary-
sourced, corroborated by two independent sources -- see docs/
assumptions.md) numeric thresholds, so it computes a provisional PASS/FAIL
when the applicant's SAT Math/TOEFL-family scores are present, always
reasoned as "based on assumed rules ... verify with MoE" rather than
VERIFIED -- this never fabricates a PASS/FAIL the knowledge base can't
actually support (CLAUDE.md rule 3), it just stops conflating "unconfirmed
official source" with "no usable information at all."
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


def _evaluate_curr_us_002(grades_by_subject: dict[str, str]) -> tuple[str, str]:
    sat_math_raw = grades_by_subject.get("sat math")
    emsat_math_raw = grades_by_subject.get("emsat math") or grades_by_subject.get("msat math")
    toefl_raw = grades_by_subject.get("toefl")
    ielts_raw = grades_by_subject.get("ielts")
    emsat_english_raw = grades_by_subject.get("emsat english") or grades_by_subject.get("english msat")

    if sat_math_raw is None and emsat_math_raw is None:
        return "UNDETERMINABLE", "No SAT Math or EmSAT Math score is present in the applicant's subject grades to evaluate against this assumed threshold."
    if toefl_raw is None and ielts_raw is None and emsat_english_raw is None:
        return "UNDETERMINABLE", "No TOEFL, IELTS, or EmSAT English score is present in the applicant's subject grades to evaluate against this assumed threshold."

    sat_math = _parse_float(sat_math_raw) if sat_math_raw is not None else None
    emsat_math = _parse_float(emsat_math_raw) if emsat_math_raw is not None else None
    meets_math = (sat_math is not None and sat_math >= 450) or (emsat_math is not None and emsat_math >= 500)

    toefl = _parse_float(toefl_raw) if toefl_raw is not None else None
    ielts = _parse_float(ielts_raw) if ielts_raw is not None else None
    emsat_english = _parse_float(emsat_english_raw) if emsat_english_raw is not None else None
    meets_english = (
        (toefl is not None and toefl >= 61)
        or (ielts is not None and ielts >= 5.0)
        or (emsat_english is not None and emsat_english >= 1100)
    )

    basis = (
        "based on assumed rules (SAT Math >= 450 or EmSAT Math >= 500, and TOEFL >= 61 or "
        "IELTS >= 5.0 or EmSAT English >= 1100 -- secondary-sourced and corroborated by two "
        "independent sources per curr-us-002, but not officially confirmed) -- verify with MoE."
    )
    if meets_math and meets_english:
        return "PASS", f"Provisional PASS, {basis}"
    return "FAIL", f"Provisional FAIL, {basis}"


def _evaluate_chunk(chunk: RuleChunk, grades_by_subject: dict[str, str]) -> tuple[str, str]:
    if chunk.id == "curr-us-002":
        return _evaluate_curr_us_002(grades_by_subject)

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
