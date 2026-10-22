"""Tests for the agent orchestrator loop: tool-call dispatch, multi-turn context retention, and iteration limits."""

from __future__ import annotations

import tools
from agent.orchestrator import MAX_TOOL_CALLS, _build_client, run_turn
from agent.validation import LLM_VERIFIER_SYSTEM_PROMPT
from config import Settings
from schemas import CaseState
from tests._fakes import FakeMessage as _FakeMessage
from tests._fakes import FakeResponse as _FakeResponse
from tests._fakes import FakeToolCall as _FakeToolCall
from tests._fakes import dispatching_create as _dispatching_create
from tools.applicant_lookup import get_applicant_record as _real_get_applicant_record

_PASS_SECTIONS = "Verified: None yet.\nAssumed: None.\nSimulated: None.\nNext step: {next_step}"


def _evaluate_eligibility_response(call_id: str, applicant_id: str, curriculum: str) -> _FakeResponse:
    """A case turn's draft is only validated after evaluate_eligibility has run this turn."""
    return _FakeResponse(
        _FakeMessage(
            tool_calls=[
                _FakeToolCall(
                    call_id, "evaluate_eligibility", {"case_state": {"applicant_id": applicant_id, "curriculum": curriculum}}
                )
            ]
        )
    )


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
            _evaluate_eligibility_response("call_2", "APP-001", "American (US High School Diploma)"),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, conversation, "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
    assert case_state.applicant_id == "APP-001"
    assert case_state.facts["name"] == "Daniel Thompson"
    assert [t.tool for t in case_state.tool_history] == ["get_applicant_record", "evaluate_eligibility"]
    assert case_state.tool_history[0].step == 1
    assert len(conversation) == 6
    assert conversation[0] == {"role": "user", "content": "Look up APP-001"}
    assert conversation[-1] == {"role": "assistant", "content": draft}


def test_run_turn_updates_case_state_on_contradictory_curriculum_without_restarting():
    case_state = CaseState(applicant_id="APP-002", curriculum="American (US High School Diploma)")
    conversation: list[dict] = [
        {"role": "user", "content": "prior turn"},
        {"role": "assistant", "content": "prior answer"},
    ]
    draft = (
        "Verified: None.\nAssumed: None.\n"
        "Simulated: Document check completed for the British (GCE) curriculum against the mock case file.\n"
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
            _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("call_1b", "get_applicant_record", {"applicant_id": "APP-002"})])),
            _evaluate_eligibility_response("call_2", "APP-002", "British (GCE)"),
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
    assert len(conversation) == 10


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
            _evaluate_eligibility_response("call_2", "APP-001", "British (GCE)"),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, conversation, "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    assert result == draft
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


def test_run_turn_tool_call_limit_triggers_escalation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()

    def always_requests_escalation(**kwargs):
        return _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall("call_x", "escalate_to_human", {"reason": "test", "case_summary": "test"})])
        )

    result = run_turn(case_state, [], "trigger loop", client=object(), model="fake-model", create_fn=always_requests_escalation)

    assert "escalated" in result.lower()
    assert len(case_state.tool_history) == MAX_TOOL_CALLS + 1
    assert case_state.tool_history[-1].tool == "escalate_to_human"
    assert case_state.tool_history[-1].args == {"reason": "tool_call_limit_reached"}
    # The reply names the step reached and the (absent) failed claim.
    assert "The last step I reached was escalating to a human reviewer (escalate_to_human)" in result
    assert "no draft answer had failed validation yet" in result


def test_run_turn_tool_call_limit_reports_last_failed_claim(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    responses = [_FakeResponse(_FakeMessage(content="An unstructured first draft."))]
    responses += [
        _FakeResponse(
            _FakeMessage(tool_calls=[_FakeToolCall(f"call_{i}", "search_requirements", {"query": f"fee question {i}"})])
        )
        for i in range(MAX_TOOL_CALLS + 1)
    ]
    create_fn = _dispatching_create(responses)

    result = run_turn(case_state, [], "What's the fee?", client=object(), model="fake-model", create_fn=create_fn)

    succeeded = [t for t in case_state.tool_history if t.tool == "search_requirements"]
    assert len(succeeded) == MAX_TOOL_CALLS  # the 13th call was refused, not executed
    assert "searching the published rules (search_requirements)" in result
    assert "the last check that failed validation was: Missing required section: 'Verified'" in result


# --- validation failure paths ------------------------------------------------


def test_run_turn_rewrites_twice_then_escalates_on_persistent_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    create_fn = _dispatching_create(
        [
            _FakeResponse(_FakeMessage(content="Sure, here's an answer with no structure at all.")),
            _FakeResponse(_FakeMessage(content="Still no structure here either.")),
            _FakeResponse(_FakeMessage(content="And a third unstructured draft.")),
        ]
    )

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert "escalated" in result.lower()
    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["validate_draft", "validate_draft", "validate_draft", "escalate_to_human"]
    assert case_state.tool_history[-1].args == {"reason": "validation_failed_after_rewrites"}
    # The trace captures the exact failure reason and the draft that failed, per attempt.
    assert "Missing required section" in case_state.tool_history[0].result_summary
    assert "Sure, here's an answer with no structure at all." in case_state.tool_history[0].result_summary
    assert "Still no structure here either." in case_state.tool_history[1].result_summary
    assert "And a third unstructured draft." in case_state.tool_history[2].result_summary


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
    # Exactly one failed attempt logged (the first draft); the revision passed, no escalation.
    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["validate_draft"]
    assert "An answer missing all the required sections." in case_state.tool_history[0].result_summary


def test_run_turn_llm_verifier_fail_triggers_revision_then_escalation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    claim = "Certificates issued abroad must be attested, and the fee is AED 50, per attest-001."
    draft = f"Verified: {claim}\nAssumed: None.\nSimulated: None.\nNext step: Provide the applicant id."
    create_fn = _dispatching_create(
        [_FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("call_1", "search_requirements", {"query": "attestation"})]))]
        + [_FakeResponse(_FakeMessage(content=draft)) for _ in range(3)],
        verifier_response=_FakeResponse(_FakeMessage(content=f'FAIL: "{claim}" -- states a fee not confirmed anywhere')),
    )

    result = run_turn(case_state, [], "Do I need attestation?", client=object(), model="fake-model", create_fn=create_fn)

    # Informational turn -> no ticket, but the reply quotes the exact failing claim and reason.
    assert case_state.escalation is None
    assert f'"{claim}" -- states a fee not confirmed anywhere' in result


def test_run_turn_case_escalation_shows_exact_failing_claims(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    claim = "Your certificate meets the official CBSE threshold."
    draft = f"Verified: {claim}\nAssumed: None.\nSimulated: None.\nNext step: none."
    create_fn = _dispatching_create(
        [
            _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("call_1", "get_applicant_record", {"applicant_id": "APP-010"})])),
            _evaluate_eligibility_response("call_2", "APP-010", "Indian (CBSE)"),
        ]
        + [_FakeResponse(_FakeMessage(content=draft)) for _ in range(3)]
    )

    result = run_turn(case_state, [], "Check APP-010", client=object(), model="fake-model", create_fn=create_fn)

    assert case_state.tool_history[-1].args == {"reason": "validation_failed_after_rewrites"}
    assert f'"{claim}"' in result
    assert "doesn't cite an OFFICIAL rule" in result


def test_run_turn_eligibility_statement_without_evaluate_eligibility_fails_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = (
        "Verified: None yet.\nAssumed: None.\nSimulated: None.\nNext step: none.\n\n"
        "Based on this, the applicant appears eligible."
    )
    create_fn = _dispatching_create([_FakeResponse(_FakeMessage(content=draft)) for _ in range(3)])

    result = run_turn(case_state, [], "hi", client=object(), model="fake-model", create_fn=create_fn)

    assert "escalated" in result.lower()


# --- tool failure paths -------------------------------------------------------


def test_run_turn_tool_failure_retries_once_then_escalates(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = _PASS_SECTIONS.format(next_step="A human reviewer has been notified and will follow up.")
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
    draft = _PASS_SECTIONS.format(next_step="A human reviewer has been notified and will follow up.")
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
            _FakeResponse(
                _FakeMessage(
                    tool_calls=[
                        _FakeToolCall(
                            "call_2",
                            "evaluate_eligibility",
                            {"case_state": {"applicant_id": "APP-001", "curriculum": "American (US High School Diploma)"}},
                        )
                    ]
                )
            ),
            _FakeResponse(_FakeMessage(content=draft)),
        ]
    )

    run_turn(case_state, [], "Look up APP-001", client=object(), model="fake-model", create_fn=create_fn)

    tool_names = [t.tool for t in case_state.tool_history]
    assert tool_names == ["get_applicant_record", "get_applicant_record", "evaluate_eligibility"]
    assert "(retry)" in case_state.tool_history[1].result_summary
    assert case_state.applicant_id == "APP-001"  # the retry succeeded and case_state was updated


# --- configurable model client -----------------------------------------------


def test_build_client_uses_azure_openai_when_endpoint_set():
    from openai import AzureOpenAI

    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="azure-key",
        azure_openai_deployment="gpt-4o",
        embedding_deployment="text-embedding-3-small",
    )

    client = _build_client(settings)

    assert isinstance(client, AzureOpenAI)


def test_build_client_uses_plain_openai_when_endpoint_unset():
    from openai import AzureOpenAI, OpenAI

    settings = Settings(openai_api_key="sk-plain-key", openai_model="gpt-4o")

    client = _build_client(settings)

    assert isinstance(client, OpenAI)
    assert not isinstance(client, AzureOpenAI)
