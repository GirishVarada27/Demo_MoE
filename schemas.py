"""Pydantic data models shared by tools, the agent orchestrator, and the UI.

Every tool returns one of these typed models rather than a raw dict
(CLAUDE.md rule 5).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class SubjectGrade(BaseModel):
    subject: str
    grade: str


class SubmittedDocument(BaseModel):
    doc_type: str
    attested: bool
    issue_date: date
    expiry_date: date | None = None
    curriculum_label_on_document: str | None = None


class ApplicantRecord(BaseModel):
    applicant_id: str
    label: Literal["SYNTHETIC"] = "SYNTHETIC"
    name: str
    country_of_origin: str
    stated_curriculum: str
    graduation_year: int
    subject_grades: list[SubjectGrade]
    submitted_documents: list[SubmittedDocument]
    expected_outcome: str


class ApplicantLookupResult(BaseModel):
    found: bool
    applicant: ApplicantRecord | None = None
    error: str | None = None


class RuleChunk(BaseModel):
    id: str
    topic: str
    text: str
    source_url: str | None = None
    label: Literal["OFFICIAL", "ASSUMPTION"]


class RuleChunkMatch(BaseModel):
    chunk: RuleChunk
    score: float


class SearchRequirementsResult(BaseModel):
    results: list[RuleChunkMatch] = []
    error: str | None = None


class InvalidDocument(BaseModel):
    doc_type: str
    reason: str


class CheckDocumentsResult(BaseModel):
    present: list[str] = []
    missing: list[str] = []
    invalid: list[InvalidDocument] = []
    error: str | None = None


class EligibilityCaseState(BaseModel):
    """The narrow applicant_id+curriculum pair evaluate_eligibility takes as
    a tool-call argument. Not to be confused with CaseState (below), the
    orchestrator's full session-level state."""

    applicant_id: str
    curriculum: str


class RuleEvaluation(BaseModel):
    chunk_id: str
    topic: str
    label: Literal["OFFICIAL", "ASSUMPTION", "NONE"]
    verdict: Literal["PASS", "FAIL", "UNDETERMINABLE"]
    reason: str


class EvaluateEligibilityResult(BaseModel):
    applicant_id: str
    curriculum: str
    rule_results: list[RuleEvaluation] = []
    undeterminable_rule_ids: list[str] = []
    error: str | None = None


class DraftApplication(BaseModel):
    draft_id: str
    applicant_id: str
    label: Literal["SIMULATED"] = "SIMULATED"
    status: Literal["draft_created"] = "draft_created"
    created_at: datetime


class CreateDraftApplicationResult(BaseModel):
    draft: DraftApplication | None = None
    error: str | None = None


class EscalationTicket(BaseModel):
    ticket_id: str
    reason: str
    case_summary: str
    label: Literal["SIMULATED"] = "SIMULATED"
    status: Literal["queued"] = "queued"
    created_at: datetime


class EscalateToHumanResult(BaseModel):
    ticket: EscalationTicket | None = None
    error: str | None = None


class ToolCallTrace(BaseModel):
    step: int
    tool: str
    args: dict
    result_summary: str
    timestamp: datetime


class CaseState(BaseModel):
    """The agent's full session-level understanding of the current case.

    Persisted by the caller (UI session state, or a test) across turns and
    passed into agent.orchestrator.run_turn each turn -- never rebuilt from
    scratch, only updated in place as new or contradictory facts appear.
    """

    applicant_id: str | None = None
    curriculum: str | None = None
    facts: dict[str, str] = {}
    documents: CheckDocumentsResult | None = None
    open_questions: list[str] = []
    assumptions: list[str] = []
    tool_history: list[ToolCallTrace] = []
