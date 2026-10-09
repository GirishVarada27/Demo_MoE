"""Tests for the agent orchestrator loop: tool-call dispatch, multi-turn context retention, and iteration limits."""

from __future__ import annotations

import tools
from agent.orchestrator import MAX_ITERATIONS, run_turn
from agent.validation import LLM_VERIFIER_SYSTEM_PROMPT
from schemas import CaseState
from tests._fakes import FakeMessage as _FakeMessage
from tests._fakes import FakeResponse as _FakeResponse
from tests._fakes import FakeToolCall as _FakeToolCall
from tests._fakes import dispatching_create as _dispatching_create
from tools.applicant_lookup import get_applicant_record as _real_get_applicant_record

_PASS_SECTIONS = "Verified: None yet.\nAssumed: None.\nSimulated: None.\nNext step: {next_step}"


def test_run_turn_no_tools_needed():
    case_state = CaseState()
    conversation: list[dict] = []
    draft = _PASS_SECTIONS.format(next_step="Tell me the applicant's id or what you'd like help with.")
    create_fn = _dispatching_create([_FakeResponse(_FakeMessage(content=draft))])

    result = run_turn(case_state, conversation, "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
    assert case_state.tool_history == []
    assert conversation == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": draft},
    ]


def test_run_turn_calls_tool_then_answers():
    case_state = CaseState()
    conversation: list[dict] = []
    draft = (
        "Verified: None yet.\nAssumed: None.\n"
        "Simulated: Applicant lookup is a SIMULATED mock action.\n"
        "Next step: Let me know what you'd like to check next."
    )
    create_fn = _dispatching_create(
        [
            _FakeResponse(
                _FakeMessage(
                    tool_calls=[_FakeToolCall("call_1", "get_applicant_record", {"applicant_id": "APP-001"})]
                )
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, conversation, "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
    assert case_state.applicant_id == "APP-001"
    assert case_state.facts["name"] == "Daniel Thompson"
    assert len(case_state.tool_history) == 1
    assert case_state.tool_history[0].tool == "get_applicant_record"
    assert case_state.tool_history[0].step == 1
    assert len(conversation) == 4
    assert conversation[0] == {"role": "user", "content": "Look up APP-001"}
    assert conversation[-1] == {"role": "assistant", "content": draft}


def test_run_turn_updates_case_state_on_contradictory_curriculum_without_restarting():
    case_state = CaseState(applicant_id="APP-002", curriculum="American (US High School Diploma)")
    conversation: list[dict] = [
        {"role": "user", "content": "prior turn"},
        {"role": "assistant", "content": "prior answer"},
    ]
    draft = (
        "Verified: Document check completed for the British (GCE) curriculum.\nAssumed: None.\n"
        "Simulated: None this turn.\n"
        "Next step: Share any remaining documents if something was flagged as missing or invalid."
    )
    create_fn = _dispatching_create(
        [
            _FakeResponse(
                _FakeMessage(
                    tool_calls=[
                        _FakeToolCall(
                            "call_1", "check_documents", {"applicant_id": "APP-002", "curriculum": "British (GCE)"}
                        )
                    ]
                )
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, conversation, "Actually it's British, not American", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
    assert case_state.curriculum == "British (GCE)"
    assert case_state.documents is not None
    # Prior conversation is preserved, not discarded -- this turn's exchange is appended after it.
    assert conversation[0] == {"role": "user", "content": "prior turn"}
    assert conversation[1] == {"role": "assistant", "content": "prior answer"}
    assert len(conversation) == 6


def test_run_turn_flags_contradictory_applicant_curriculum_as_open_question():
    case_state = CaseState(curriculum="British (GCE)")
    conversation: list[dict] = []
    draft = (
        "Verified: None yet.\nAssumed: None.\nSimulated: Applicant lookup is SIMULATED.\n"
        "Next step: Please confirm which curriculum is correct before I proceed."
    )
    create_fn = _dispatching_create(
        [
            _FakeResponse(
                _FakeMessage(tool_calls=[_FakeToolCall("call_1", "get_applicant_record", {"applicant_id": "APP-001"})])
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, conversation, "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    assert case_state.curriculum == "British (GCE)"  # not silently overwritten
    assert any("differs from the curriculum currently being evaluated" in q for q in case_state.open_questions)


def test_run_turn_injects_case_state_into_context():
    case_state = CaseState(applicant_id="APP-001", curriculum="American (US High School Diploma)")
    captured: dict = {}
    draft = _PASS_SECTIONS.format(next_step="none.")

    def create(**kwargs):
        if kwargs["messages"][0].get("content") == LLM_VERIFIER_SYSTEM_PROMPT:
            return _FakeResponse(_FakeMessage(content="PASS"))
        captured["messages"] = kwargs["messages"]
        return _FakeResponse(_FakeMessage(content=draft))

    run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create)

    system_contents = [m["content"] for m in captured["messages"] if m["role"] == "system"]
    assert any("APP-001" in content for content in system_contents)


def test_run_turn_max_iterations_triggers_escalation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()

    def always_requests_escalation(**kwargs):
        return _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall("call_x", "escalate_to_human", {"reason": "test", "case_summary": "test"})])
        )

    result = run_turn(case_state, [], "trigger loop", client=object(), model="fake-model", create_fn=always_requests_escalation)

    assert "escalated" in result.lower()
    assert len(case_state.tool_history) == MAX_ITERATIONS + 1
    assert case_state.tool_history[-1].tool == "escalate_to_human"
    assert case_state.tool_history[-1].args == {"reason": "max_iterations_reached"}


# --- validation failure paths ------------------------------------------------


def test_run_turn_revises_once_then_escalates_on_persistent_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    create_fn = _dispatching_create(
        [
            _FakeResponse(_FakeMessage(content="Sure, here's an answer with no structure at all.")),
            _FakeResponse(_FakeMessage(content="Still no structure here either.")),
        ]
    )

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert "escalated" in result.lower()
    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["escalate_to_human"]
    assert case_state.tool_history[0].args == {"reason": "validation_failed_twice"}


def test_run_turn_revision_succeeds_on_second_draft():
    case_state = CaseState()
    good_draft = _PASS_SECTIONS.format(next_step="Provide the applicant id.")
    create_fn = _dispatching_create(
        [
            _FakeResponse(_FakeMessage(content="An answer missing all the required sections.")),
            _FakeResponse(_FakeMessage(content=good_draft)),
        ]
    )

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert result == good_draft
    assert case_state.tool_history == []  # no escalation needed -- the revision passed


def test_run_turn_llm_verifier_fail_triggers_revision_then_escalation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = _PASS_SECTIONS.format(next_step="Provide the applicant id.")
    create_fn = _dispatching_create(
        [_FakeResponse(_FakeMessage(content=draft)), _FakeResponse(_FakeMessage(content=draft))],
        verifier_response=_FakeResponse(_FakeMessage(content="FAIL: states a fee not confirmed anywhere")),
    )

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert "escalated" in result.lower()
    assert case_state.tool_history[-1].tool == "escalate_to_human"
    assert case_state.tool_history[-1].args == {"reason": "validation_failed_twice"}


def test_run_turn_eligibility_statement_without_evaluate_eligibility_fails_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = (
        "Verified: None yet.\nAssumed: None.\nSimulated: None.\nNext step: none.\n\n"
        "Based on this, the applicant appears eligible."
    )
    create_fn = _dispatching_create([_FakeResponse(_FakeMessage(content=draft)), _FakeResponse(_FakeMessage(content=draft))])

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert "escalated" in result.lower()


# --- tool failure paths -------------------------------------------------------


def test_run_turn_tool_failure_retries_once_then_escalates(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = _PASS_SECTIONS.format(next_step="I've escalated this; a human will follow up.")
    create_fn = _dispatching_create(
        [
            _FakeResponse(
                _FakeMessage(
                    tool_calls=[
                        _FakeToolCall(
                            "call_1", "get_applicant_record", {"applicant_id": "APP-001", "simulate_failure": True}
                        )
                    ]
                )
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, [], "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["get_applicant_record", "get_applicant_record", "escalate_to_human"]
    assert "(retry)" in case_state.tool_history[1].result_summary
    assert case_state.tool_history[2].args == {"reason": "tool_failure:get_applicant_record"}
    # The failed tool never updated case_state -- it never found a real applicant.
    assert case_state.applicant_id is None


def test_run_turn_inject_failures_forces_simulate_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = _PASS_SECTIONS.format(next_step="I've escalated this; a human will follow up.")
    # The model requests a normal, non-failing lookup -- inject_failures overrides it anyway.
    create_fn = _dispatching_create(
        [
            _FakeResponse(
                _FakeMessage(tool_calls=[_FakeToolCall("call_1", "get_applicant_record", {"applicant_id": "APP-001"})])
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    run_turn(
        case_state,
        [],
        "Look up APP-001",
        client=object(),
        model="fake-model",
        create_fn=create_fn,
        inject_failures=True,
    )

    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["get_applicant_record", "get_applicant_record", "escalate_to_human"]
    assert case_state.applicant_id is None  # forced failure -- the lookup never actually succeeded


def test_run_turn_tool_failure_recovers_on_retry(monkeypatch):
    case_state = CaseState()
    call_count = {"n": 0}

    def flaky_get_applicant_record(applicant_id, simulate_failure=False):
        call_count["n"] += 1
        return _real_get_applicant_record(applicant_id, simulate_failure=(call_count["n"] == 1))

    monkeypatch.setitem(tools.TOOL_FUNCTIONS, "get_applicant_record", flaky_get_applicant_record)

    draft = _PASS_SECTIONS.format(next_step="none.")
    create_fn = _dispatching_create(
        [
            _FakeResponse(
                _FakeMessage(tool_calls=[_FakeToolCall("call_1", "get_applicant_record", {"applicant_id": "APP-001"})])
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, [], "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["get_applicant_record", "get_applicant_record"]
    assert "(retry)" in case_state.tool_history[1].result_summary
    assert case_state.applicant_id == "APP-001"  # the retry succeeded and case_state was updated
