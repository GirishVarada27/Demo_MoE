"""Tests for change-of-circumstances handling: the agent used to ignore a
new fact the user reported mid-conversation (e.g. "I can't get my attested
transcript") and just repeat stale cached document-check info. Fixes:

1. CaseState.user_reported_facts (schemas.py) + the record_user_fact tool
   (tools/user_facts.py) make a user-reported fact structural, not just
   conversation text, explicitly labeled "user_reported".
2. agent/orchestrator.py::_apply_result_to_case_state detects a conflict
   when a recorded fact_key matches an existing tool-reported fact under a
   different value, and surfaces it in open_questions.
3. agent/prompts.py tells the model to re-run the checks a new fact
   affects rather than reuse a stale cache, and to offer concrete options
   (alternative route, an exception path only if it genuinely applies,
   escalation) when a requirement can't be met through the normal path.
4. Duplicate identical tool calls within one turn are deduplicated
   (agent/orchestrator.py's seen_calls), and a bare greeting needs no tool
   calls at all.
5. create_draft_application is only valid without a caveat when
   evaluate_eligibility actually reached PASS/FAIL this session
   (agent/validation.py::check_draft_application_caveated).
"""

from __future__ import annotations

from agent.orchestrator import run_turn
from agent.prompts import SYSTEM_PROMPT
from agent.validation import check_draft_application_caveated
from schemas import CaseState, ToolCallTrace
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create
from datetime import datetime, timezone


def _tool_names(case_state: CaseState, start: int = 0) -> list[str]:
    return [entry.tool for entry in case_state.tool_history[start:]]


def _trace(tool: str, result_summary: str, args: dict | None = None) -> ToolCallTrace:
    return ToolCallTrace(step=1, tool=tool, args=args or {}, result_summary=result_summary, timestamp=datetime.now(timezone.utc))


# --- 1 & 2 & 3 & 4a. The transcript scenario: change of circumstances mid-conversation ---


def test_transcript_attestation_blocker_is_recorded_and_changes_the_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    conversation: list[dict] = []

    turn1_draft = (
        "Verified: Your transcript is on file but not attested; certificate, passport, and "
        "Emirates ID are all present and valid.\nAssumed: None.\n"
        "Simulated: Applicant and document records were looked up from the mock case file.\n"
        "Next step: Get your transcript attested and resubmit it."
    )
    create_fn_1 = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-002"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall("c2", "check_documents", {"applicant_id": "APP-002", "curriculum": "British (GCE)"})
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=turn1_draft)),
        ]
    )
    run_turn(
        case_state, conversation, "Can you check my documents? My ID is APP-002.", client=object(), model="fake-model", create_fn=create_fn_1
    )
    steps_after_turn1 = len(case_state.tool_history)

    turn2_draft = (
        "Verified: Your transcript remains on file as not attested; certificate, passport, and "
        "Emirates ID are present and valid.\nAssumed: None.\n"
        "Simulated: Since you reported being unable to obtain attestation through the normal "
        "process, I searched for an alternative route and filed an escalation ticket.\n"
        "Next step: A human reviewer will follow up on alternative attestation options or "
        "exception paths."
    )
    create_fn_2 = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c3",
                            "record_user_fact",
                            {
                                "fact_key": "transcript_attestation",
                                "fact_value": "Applicant cannot obtain an attested transcript through normal means.",
                            },
                        )
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c4", "check_documents", {"applicant_id": "APP-002", "curriculum": "British (GCE)"}
                        )
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c5", "search_requirements", {"query": "alternative to transcript attestation requirement"}
                        )
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c6",
                            "escalate_to_human",
                            {
                                "reason": "Applicant cannot obtain attestation for a required transcript.",
                                "case_summary": "APP-002, British curriculum, transcript attestation unobtainable.",
                            },
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=turn2_draft)),
        ]
    )
    result = run_turn(
        case_state, conversation, "Actually, I can't get my attested transcript.", client=object(), model="fake-model", create_fn=create_fn_2
    )

    # 1. The fact is structurally recorded, explicitly labeled, not just conversation text.
    assert case_state.user_reported_facts["transcript_attestation"].label == "user_reported"
    assert "cannot obtain an attested transcript" in case_state.user_reported_facts["transcript_attestation"].value

    # 3. The plan changed: re-ran check_documents, checked for an alternative, then escalated --
    # it didn't just repeat turn 1's advice.
    turn2_tools = _tool_names(case_state, start=steps_after_turn1)
    assert turn2_tools == ["record_user_fact", "check_documents", "search_requirements", "escalate_to_human"]

    assert result == turn2_draft  # validated and returned as-is
    # Conversation isn't restarted -- both turns' user messages are preserved.
    user_messages = [m["content"] for m in conversation if m["role"] == "user"]
    assert any("Can you check my documents" in m for m in user_messages)
    assert any("can't get my attested transcript" in m for m in user_messages)


def test_record_user_fact_conflicting_with_tool_reported_fact_is_flagged():
    """A user-reported fact that contradicts an existing tool-reported fact
    under the same key must be stated explicitly as a conflict, and the
    user-reported value takes precedence for planning (schemas.CaseState
    isn't overwritten silently either way -- the conflict is surfaced)."""
    case_state = CaseState()
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none."
    create_fn = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-002"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c2",
                            "record_user_fact",
                            {"fact_key": "stated_curriculum_on_file", "fact_value": "American (US High School Diploma)"},
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, [], "Look up APP-002", client=object(), model="fake-model", create_fn=create_fn)

    assert case_state.facts["stated_curriculum_on_file"] == "British (GCE)"  # tool-reported fact, untouched
    assert case_state.user_reported_facts["stated_curriculum_on_file"].value == "American (US High School Diploma)"
    assert any(
        "conflicts with the on-file fact 'British (GCE)'" in q for q in case_state.open_questions
    )


# --- 4b. Duplicate identical tool calls in one turn are not re-invoked ---


def test_duplicate_identical_tool_call_in_one_turn_is_not_reinvoked():
    case_state = CaseState()
    draft = "Verified: None.\nAssumed: None.\nSimulated: None.\nNext step: none."
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall("c1", "check_documents", {"applicant_id": "APP-002", "curriculum": "British (GCE)"}),
                        FakeToolCall("c2", "check_documents", {"applicant_id": "APP-002", "curriculum": "British (GCE)"}),
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, [], "Check my documents twice, APP-002.", client=object(), model="fake-model", create_fn=create_fn)

    tool_names = _tool_names(case_state)
    assert tool_names == ["check_documents", "check_documents"]
    assert "present (" in case_state.tool_history[0].result_summary  # the real invocation
    assert case_state.tool_history[1].result_summary == "duplicate call this turn -- reused prior result, not re-invoked"


# --- 4c. A bare greeting needs no tool calls ---


def test_bare_greeting_runs_no_checks():
    case_state = CaseState()
    draft = "Verified: None yet.\nAssumed: None.\nSimulated: None.\nNext step: How can I help with your certificate equivalency case?"
    create_fn = dispatching_create([FakeResponse(FakeMessage(content=draft))])

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert case_state.tool_history == []
    assert result == draft


def test_prompt_instructs_no_checks_on_bare_greeting_and_recording_user_facts():
    assert "A bare greeting with no question in it" in SYSTEM_PROMPT
    assert "needs no tool calls at all" in SYSTEM_PROMPT
    assert "record_user_fact" in SYSTEM_PROMPT
    assert "re-run only the check(s)" in SYSTEM_PROMPT


# --- 5. create_draft_application requires determinable eligibility, or a caveat ---


def test_draft_application_without_determinable_eligibility_requires_caveat():
    case_state = CaseState(
        tool_history=[
            _trace("create_draft_application", "draft DRAFT-TEST created (SIMULATED)"),
            _trace(
                "evaluate_eligibility",
                "verdicts=[curr-uk-001[ASSUMPTION]=UNDETERMINABLE], undeterminable=['curr-uk-001']",
            ),
        ]
    )
    uncaveated = "A draft application has been created and the applicant is good to go."
    assert check_draft_application_caveated(uncaveated, case_state)

    caveated = "A draft application has been created; this is provisional pending confirmation of eligibility."
    assert check_draft_application_caveated(caveated, case_state) == []


def test_draft_application_with_determinable_eligibility_needs_no_caveat():
    case_state = CaseState(
        tool_history=[
            _trace("evaluate_eligibility", "verdicts=[except-002[OFFICIAL]=PASS], undeterminable=[]"),
            _trace("create_draft_application", "draft DRAFT-TEST created (SIMULATED)"),
        ]
    )
    assert check_draft_application_caveated("A draft application has been created.", case_state) == []


def test_draft_application_without_any_eligibility_check_requires_caveat():
    case_state = CaseState(tool_history=[_trace("create_draft_application", "draft DRAFT-TEST created (SIMULATED)")])
    assert check_draft_application_caveated("A draft application has been created.", case_state)
