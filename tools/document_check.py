"""Tool: check_documents.

Compares an applicant's submitted documents against the baseline document
set confirmed by the OFFICIAL knowledge chunks (doc-001/doc-002/doc-003:
Emirates ID, passport, grade 10-12 certificates, with attestation and
translation where required). Deliberately curriculum-agnostic: curr-us-001
confirms a baseline SAT Math/TOEFL *score* requirement for American
curriculum, but no OFFICIAL chunk states that a score-report *document*
must be uploaded, so this tool does not invent one (CLAUDE.md rule 3) --
score-threshold evaluation belongs to evaluate_eligibility, which reads
subject_grades instead.

A document is "invalid" (present but flawed) rather than silently
"present" when: it needs attestation and isn't attested, it has an expiry
date in the past, or its curriculum_label_on_document disagrees with the
curriculum this check is run against.
"""

from __future__ import annotations

from datetime import date

from schemas import CheckDocumentsResult, InvalidDocument
from tools._curriculum import classify_curriculum_family
from tools.applicant_lookup import get_applicant_record

BASE_REQUIRED_DOC_TYPES = ["grade_12_certificate", "transcript", "passport_copy", "emirates_id_copy"]
ATTESTATION_REQUIRED_DOC_TYPES = {"grade_12_certificate", "transcript"}

CHECK_DOCUMENTS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_documents",
        "description": "Check a synthetic applicant's submitted documents against the officially-confirmed required set, returning which are present (and valid), missing, or invalid (unattested, expired, or curriculum-mismatched against the given curriculum).",
        "parameters": {
            "type": "object",
            "properties": {
                "applicant_id": {"type": "string", "description": "The applicant's id, e.g. 'APP-001'."},
                "curriculum": {"type": "string", "description": "Curriculum to check documents against, e.g. 'American (US High School Diploma)'."},
                "simulate_failure": {
                    "type": "boolean",
                    "description": "If true, return a simulated failure instead of checking documents.",
                    "default": False,
                },
            },
            "required": ["applicant_id", "curriculum"],
        },
    },
}


def check_documents(applicant_id: str, curriculum: str, simulate_failure: bool = False) -> CheckDocumentsResult:
    if simulate_failure:
        return CheckDocumentsResult(error="Simulated document-check failure (simulate_failure=True).")

    lookup = get_applicant_record(applicant_id)
    if not lookup.found:
        return CheckDocumentsResult(error=lookup.error)
    applicant = lookup.applicant

    family = classify_curriculum_family(curriculum)
    docs_by_type = {doc.doc_type: doc for doc in applicant.submitted_documents}

    present: list[str] = []
    missing: list[str] = []
    invalid: list[InvalidDocument] = []

    for doc_type in BASE_REQUIRED_DOC_TYPES:
        doc = docs_by_type.get(doc_type)
        if doc is None:
            missing.append(doc_type)
            continue

        reasons: list[str] = []
        if doc_type in ATTESTATION_REQUIRED_DOC_TYPES and not doc.attested:
            reasons.append("not attested")
        if doc.expiry_date is not None and doc.expiry_date < date.today():
            reasons.append(f"expired on {doc.expiry_date.isoformat()}")
        if doc.curriculum_label_on_document:
            doc_family = classify_curriculum_family(doc.curriculum_label_on_document)
            if family and doc_family and doc_family != family:
                reasons.append(
                    f"curriculum label on document ('{doc.curriculum_label_on_document}') "
                    f"does not match stated curriculum ('{curriculum}')"
                )

        if reasons:
            invalid.append(InvalidDocument(doc_type=doc_type, reason="; ".join(reasons)))
        else:
            present.append(doc_type)

    return CheckDocumentsResult(present=present, missing=missing, invalid=invalid)
