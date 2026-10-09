"""Tools package.

Typed, independently callable functions the agent selects at runtime --
never called in a hardcoded sequence (CLAUDE.md rule 1). Each tool returns
a Pydantic model from schemas.py and accepts a simulate_failure flag
(CLAUDE.md rule 5). TOOL_SCHEMAS/TOOL_FUNCTIONS are what the orchestrator
registers with Azure OpenAI function calling.
"""

from __future__ import annotations

from tools.applicant_lookup import GET_APPLICANT_RECORD_TOOL_SCHEMA, get_applicant_record
from tools.document_check import CHECK_DOCUMENTS_TOOL_SCHEMA, check_documents
from tools.draft_application import CREATE_DRAFT_APPLICATION_TOOL_SCHEMA, create_draft_application
from tools.equivalency_calc import EVALUATE_ELIGIBILITY_TOOL_SCHEMA, evaluate_eligibility
from tools.escalation import ESCALATE_TO_HUMAN_TOOL_SCHEMA, escalate_to_human
from tools.search_rules import SEARCH_REQUIREMENTS_TOOL_SCHEMA, search_requirements

TOOL_SCHEMAS = [
    SEARCH_REQUIREMENTS_TOOL_SCHEMA,
    GET_APPLICANT_RECORD_TOOL_SCHEMA,
    CHECK_DOCUMENTS_TOOL_SCHEMA,
    EVALUATE_ELIGIBILITY_TOOL_SCHEMA,
    CREATE_DRAFT_APPLICATION_TOOL_SCHEMA,
    ESCALATE_TO_HUMAN_TOOL_SCHEMA,
]

TOOL_FUNCTIONS = {
    "search_requirements": search_requirements,
    "get_applicant_record": get_applicant_record,
    "check_documents": check_documents,
    "evaluate_eligibility": evaluate_eligibility,
    "create_draft_application": create_draft_application,
    "escalate_to_human": escalate_to_human,
}
