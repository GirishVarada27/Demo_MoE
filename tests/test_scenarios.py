"""End-to-end scenario tests for agent.orchestrator.run_turn.

Each test drives a realistic user message through the real tool-calling
loop (validator, retry/escalation, case-state updates included) with a
scripted fake model standing in for the real LLM's tool choices. Assertions
check BEHAVIOR -- which tools were called and in what order, how case_state
changed, whether escalation fired, whether the structural answer contract
holds -- never the exact prose of a draft answer, since that's authored
fixture text standing in for what a real model would say.
"""

from __future__ import annotations

from agent.orchestrator import run_turn
from agent.validation import check_required_sections
from schemas import CaseState
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create


def _tool_names(case_state: CaseState, start: int = 0) -> list[str]:
    return [entry.tool for entry in case_state.tool_history[start:]]


# --- "What documents do I need?" -> search_requirements only -----------------


def test_documents_question_uses_search_requirements_only(monkeypatch):
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])
    case_state = CaseState()
    draft = (
        "Verified: You'll need your original grade 10-12 certificates, an Emirates ID copy, and a "
        "passport copy; foreign-language certificates also need a certified translation.\n"
        "Assumed: None.\nSimulated: None.\n"
        "Next step: Gather these documents, then share your applicant id so I can check your file."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c1", "search_requirements", {"query": "What documents are required for certificate equivalency?"}
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, [], "What documents do I need?", client=object(), model="fake-model", create_fn=create_fn)

    assert _tool_names(case_state) == ["search_requirements"]
    assert check_required_sections(result) == []


# --- "Am I missing anything?" -> get_applicant_record + check_documents ------


def test_missing_anything_question_looks_up_applicant_then_checks_documents():
    case_state = CaseState()
    draft = (
        "Verified: Your transcript was submitted but is not yet attested; your certificate, "
        "passport, and Emirates ID are all present and valid.\nAssumed: None.\n"
        "Simulated: Applicant and document records were looked up from the mock case file.\n"
        "Next step: Get your transcript attested and resubmit it."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-002"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall("c2", "check_documents", {"applicant_id": "APP-002", "curriculum": "British (GCE)"})
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(
        case_state, [], "Am I missing anything? My applicant id is APP-002.", client=object(), model="fake-model", create_fn=create_fn
    )

    assert _tool_names(case_state) == ["get_applicant_record", "check_documents"]
    # The reported gap comes from the real tool result, not the fixture prose.
    assert case_state.documents is not None
    assert any(doc.doc_type == "transcript" for doc in case_state.documents.invalid)
    assert case_state.documents.missing == []
    assert check_required_sections(result) == []


# --- "Actually I can't get my attested transcript" -> revalidation + changed plan


def test_cannot_get_attestation_triggers_revalidation_and_changed_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    conversation: list[dict] = []

    turn1_draft = (
        "Verified: Your transcript was submitted but is not yet attested; certificate, passport, "
        "and Emirates ID are all present and valid.\nAssumed: None.\n"
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

    assert any(doc.doc_type == "transcript" for doc in case_state.documents.invalid)
    steps_after_turn1 = len(case_state.tool_history)

    turn2_draft = (
        "Verified: Attestation cannot be obtained, so standard document verification cannot proceed "
        "for this requirement.\nAssumed: None.\n"
        "Simulated: An escalation ticket was filed in the mock case-management queue.\n"
        "Next step: A human reviewer will follow up about alternative ways to verify your transcript."
    )
    create_fn_2 = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c3",
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
        case_state, conversation, "Actually I can't get my attested transcript.", client=object(), model="fake-model", create_fn=create_fn_2
    )

    # The plan changed -- turn 2 escalates rather than repeating turn 1's check_documents.
    assert _tool_names(case_state, start=steps_after_turn1) == ["escalate_to_human"]
    assert check_required_sections(result) == []
    # Conversation isn't restarted -- both turns' user messages are still present.
    user_messages = [m["content"] for m in conversation if m["role"] == "user"]
    assert any("Can you check my documents" in m for m in user_messages)
    assert any("can't get my attested transcript" in m for m in user_messages)


# --- contradictory input -> clarifying question (open_question), not a guess --


def test_contradictory_curriculum_is_detected_and_flagged_as_open_question():
    case_state = CaseState()
    draft = (
        "Verified: None yet -- your on-file record shows a different curriculum than you stated.\n"
        "Assumed: None.\nSimulated: Record lookup is against mock data.\n"
        "Next step: Your on-file record lists a different curriculum than you stated -- which is correct?"
    )
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c1", "check_documents", {"applicant_id": "APP-005", "curriculum": "American (US High School Diploma)"}
                        )
                    ]
                )
            ),
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c2", "get_applicant_record", {"applicant_id": "APP-005"})])),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(
        case_state,
        [],
        "I completed the American curriculum abroad. My applicant ID is APP-005. Am I missing anything?",
        client=object(),
        model="fake-model",
        create_fn=create_fn,
    )

    # APP-005's real on-file curriculum is British (GCE) -- a genuine contradiction with
    # what the user stated, surfaced as an open question rather than silently resolved.
    assert case_state.curriculum == "American (US High School Diploma)"  # not silently overwritten
    assert any("differs from the curriculum currently being evaluated" in q for q in case_state.open_questions)
    assert check_required_sections(result) == []


# --- tool failure -> graceful handling + escalation --------------------------


def test_tool_failure_is_handled_gracefully_and_escalated(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    case_state = CaseState()
    draft = (
        "Verified: None.\nAssumed: None.\n"
        "Simulated: An escalation ticket was filed in the mock case-management queue after the "
        "applicant lookup failed twice.\n"
        "Next step: A human reviewer will follow up and complete this lookup manually."
    )
    create_fn = dispatching_create(
        [
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-003", "simulate_failure": True})
                    ]
                )
            ),
            FakeResponse(FakeMessage(content=draft)),
        ]
    )

    result = run_turn(case_state, [], "Can you check my documents? My ID is APP-003.", client=object(), model="fake-model", create_fn=create_fn)

    assert _tool_names(case_state) == ["get_applicant_record", "get_applicant_record", "escalate_to_human"]
    assert "(retry)" in case_state.tool_history[1].result_summary
    assert case_state.tool_history[-1].args == {"reason": "tool_failure:get_applicant_record"}
    assert case_state.applicant_id is None  # the failed lookup never actually resolved
    assert check_required_sections(result) == []


# --- unsupported request -> declined with a next step ------------------------


def test_unsupported_request_is_declined_with_a_next_step():
    case_state = CaseState()
    draft = (
        "Verified: None.\nAssumed: None.\nSimulated: None.\n"
        "Next step: Visa applications are outside this service's scope -- please contact "
        "immigration services directly. I can only help with certificate equivalency for Grade "
        "12 qualifications completed abroad."
    )
    create_fn = dispatching_create([FakeResponse(FakeMessage(content=draft))])

    result = run_turn(
        case_state, [], "Can you also help me apply for a US student visa?", client=object(), model="fake-model", create_fn=create_fn
    )

    assert case_state.tool_history == []  # nothing in scope to call a tool for
    assert check_required_sections(result) == []
    sections_present = {name for name in ["Verified", "Assumed", "Simulated", "Next step"] if name.lower() in result.lower()}
    assert "Next step" in sections_present
