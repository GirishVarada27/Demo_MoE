"""Tests for individual tool functions, including their simulate_failure paths."""

from __future__ import annotations

import json

import tools
from schemas import EligibilityCaseState
from tools.applicant_lookup import get_applicant_record
from tools.document_check import check_documents
from tools.draft_application import create_draft_application
from tools.equivalency_calc import evaluate_eligibility
from tools.escalation import escalate_to_human
from tools.search_rules import load_rule_chunks, search_requirements

# --- registry -----------------------------------------------------------


def test_tool_registry_is_consistent():
    assert len(tools.TOOL_SCHEMAS) == 6
    assert set(tools.TOOL_FUNCTIONS) == {
        "search_requirements",
        "get_applicant_record",
        "check_documents",
        "evaluate_eligibility",
        "create_draft_application",
        "escalate_to_human",
    }
    schema_names = {schema["function"]["name"] for schema in tools.TOOL_SCHEMAS}
    assert schema_names == set(tools.TOOL_FUNCTIONS)


# --- search_requirements --------------------------------------------------


def test_load_rule_chunks_has_official_and_assumption_labels():
    chunks = load_rule_chunks()
    labels = {chunk.label for chunk in chunks}
    assert labels == {"OFFICIAL", "ASSUMPTION"}


def test_search_requirements_simulate_failure():
    result = search_requirements("anything", simulate_failure=True)
    assert result.error is not None
    assert result.results == []


def test_search_requirements_ranks_relevant_chunk_first(monkeypatch):
    target = next(c for c in load_rule_chunks() if c.id == "attest-002")

    def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if text == target.text else [0.0, 1.0] for text in texts]

    monkeypatch.setattr("tools.search_rules._embed", fake_embed)
    result = search_requirements(target.text, top_k=3)

    assert result.error is None
    # Irrelevant chunks (score 0.0, below MIN_RELEVANCE_SCORE) are filtered out, not padded in.
    assert len(result.results) == 1
    assert result.results[0].chunk.id == "attest-002"
    assert result.results[0].score == 1.0


def test_search_requirements_returns_empty_when_nothing_relevant(monkeypatch):
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[0.0, 0.0] for _ in texts])

    result = search_requirements("completely unrelated query")

    assert result.error is None
    assert result.results == []


# --- get_applicant_record -------------------------------------------------


def test_get_applicant_record_found():
    result = get_applicant_record("APP-001")
    assert result.found is True
    assert result.applicant.applicant_id == "APP-001"
    assert result.applicant.label == "SYNTHETIC"


def test_get_applicant_record_not_found():
    result = get_applicant_record("APP-999")
    assert result.found is False
    assert "APP-999" in result.error


def test_get_applicant_record_simulate_failure():
    result = get_applicant_record("APP-001", simulate_failure=True)
    assert result.found is False
    assert result.error is not None


# --- check_documents -------------------------------------------------------


def test_check_documents_complete_file():
    result = check_documents("APP-001", "American (US High School Diploma)")
    assert result.error is None
    assert result.missing == []
    assert result.invalid == []
    assert set(result.present) == {
        "grade_12_certificate",
        "transcript",
        "passport_copy",
        "emirates_id_copy",
    }


def test_check_documents_missing_attested_transcript():
    result = check_documents("APP-002", "British (GCE)")
    invalid_types = {doc.doc_type: doc.reason for doc in result.invalid}
    assert "transcript" in invalid_types
    assert "not attested" in invalid_types["transcript"]


def test_check_documents_missing_identity_document():
    result = check_documents("APP-003", "American (US High School Diploma)")
    assert "emirates_id_copy" in result.missing


def test_check_documents_contradictory_curriculum():
    result = check_documents("APP-004", "American (US High School Diploma)")
    invalid_types = {doc.doc_type: doc.reason for doc in result.invalid}
    assert "grade_12_certificate" in invalid_types
    assert "does not match stated curriculum" in invalid_types["grade_12_certificate"]
    assert "transcript" in invalid_types
    assert result.missing == []


def test_check_documents_expired_document():
    result = check_documents("APP-005", "British (GCE)")
    invalid_types = {doc.doc_type: doc.reason for doc in result.invalid}
    assert "passport_copy" in invalid_types
    assert "expired on 2025-01-15" in invalid_types["passport_copy"]


def test_check_documents_unknown_applicant():
    result = check_documents("APP-999", "American (US High School Diploma)")
    assert result.error is not None


def test_check_documents_simulate_failure():
    result = check_documents("APP-001", "American (US High School Diploma)", simulate_failure=True)
    assert result.error is not None


# --- evaluate_eligibility ---------------------------------------------------


def test_evaluate_eligibility_american_exception_case_passes():
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-007", curriculum="American (US High School Diploma)"))
    by_id = {r.chunk_id: r for r in result.rule_results}
    assert by_id["except-002"].verdict == "PASS"
    assert by_id["except-001"].verdict == "UNDETERMINABLE"


def test_evaluate_eligibility_american_without_msat_is_undeterminable():
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-001", curriculum="American (US High School Diploma)"))
    by_id = {r.chunk_id: r for r in result.rule_results}
    assert by_id["except-002"].verdict == "UNDETERMINABLE"
    assert "curr-us-002" in result.undeterminable_rule_ids


def test_evaluate_eligibility_unsupported_curriculum_returns_none_chunk():
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-006", curriculum="WAEC (West African Senior School Certificate)"))
    assert [r.chunk_id for r in result.rule_results] == ["NONE"]
    assert result.undeterminable_rule_ids == ["NONE"]


def test_evaluate_eligibility_indian_curriculum_flags_assumption():
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-010", curriculum="Indian (CBSE)"))
    by_id = {r.chunk_id: r for r in result.rule_results}
    assert by_id["curr-in-001"].verdict == "UNDETERMINABLE"
    assert by_id["curr-in-001"].label == "ASSUMPTION"


def test_evaluate_eligibility_unknown_applicant():
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-999", curriculum="American (US High School Diploma)"))
    assert result.error is not None


def test_evaluate_eligibility_simulate_failure():
    result = evaluate_eligibility(EligibilityCaseState(applicant_id="APP-001", curriculum="American (US High School Diploma)"), simulate_failure=True)
    assert result.error is not None


# --- create_draft_application -----------------------------------------------


def test_create_draft_application_writes_to_store(tmp_path, monkeypatch):
    store = tmp_path / "draft_applications.json"
    monkeypatch.setattr("tools.draft_application._STORE_PATH", store)

    result = create_draft_application("APP-001")

    assert result.error is None
    assert result.draft.label == "SIMULATED"
    assert result.draft.applicant_id == "APP-001"
    assert result.draft.draft_id.startswith("DRAFT-")
    assert json.loads(store.read_text(encoding="utf-8"))


def test_create_draft_application_unknown_applicant(tmp_path, monkeypatch):
    store = tmp_path / "draft_applications.json"
    monkeypatch.setattr("tools.draft_application._STORE_PATH", store)

    result = create_draft_application("APP-999")

    assert result.error is not None
    assert not store.exists()


def test_create_draft_application_simulate_failure(tmp_path, monkeypatch):
    store = tmp_path / "draft_applications.json"
    monkeypatch.setattr("tools.draft_application._STORE_PATH", store)

    result = create_draft_application("APP-001", simulate_failure=True)

    assert result.error is not None
    assert not store.exists()


# --- escalate_to_human -------------------------------------------------------


def test_escalate_to_human_writes_ticket(tmp_path, monkeypatch):
    queue = tmp_path / "escalation_queue.json"
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", queue)

    result = escalate_to_human(reason="Unsupported curriculum", case_summary="APP-006, Nigeria/WAEC")

    assert result.error is None
    assert result.ticket.label == "SIMULATED"
    assert result.ticket.status == "queued"
    assert result.ticket.ticket_id.startswith("TCK-")
    stored = json.loads(queue.read_text(encoding="utf-8"))
    assert len(stored) == 1
    assert stored[0]["reason"] == "Unsupported curriculum"


def test_escalate_to_human_simulate_failure(tmp_path, monkeypatch):
    queue = tmp_path / "escalation_queue.json"
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", queue)

    result = escalate_to_human(reason="x", case_summary="y", simulate_failure=True)

    assert result.error is not None
    assert not queue.exists()
