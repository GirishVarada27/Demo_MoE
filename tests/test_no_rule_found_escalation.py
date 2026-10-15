"""Regression tests for testing APP-006 (Nigeria / WAEC curriculum, which has
no matching rule chunk in the knowledge base):

1. Before the agent makes any claim about what rules exist or don't for a
   curriculum, search_requirements must actually be called for it this
   session -- enforced in the loop itself (agent.orchestrator's
   _handle_no_rule_found runs search_requirements automatically the moment
   evaluate_eligibility signals "no rule chunk at all", rather than letting
   the model assert that conclusion unverified).
2. When search_requirements also comes back empty, the orchestrator records
   reason="no_rule_found:<CURRICULUM>/<COUNTRY>" on CaseState and escalates
   directly off the back of that -- not via a validation failure. At most
   one ticket is filed.
3. The chat-facing answer is plain language: no section names
   ("'Verified' section"), no validator wording ("no OFFICIAL knowledge
   chunk was retrieved"), just what couldn't be confirmed, why, and the
   ticket reference.
"""

from __future__ import annotations

from agent.orchestrator import _handle_no_rule_found, run_turn
from schemas import CaseState, EvaluateEligibilityResult, RuleEvaluation
from tests._fakes import FakeMessage, FakeResponse, FakeToolCall, dispatching_create


def _fake_embed_no_matches(texts: list[str]) -> list[list[float]]:
    """Orthogonal one-hot vectors -- the query (always index 0) has zero
    cosine similarity with every chunk, so search_requirements always comes
    back with 0 results regardless of query text, deterministically
    simulating "nothing in the knowledge base matches"."""
    vectors = []
    for i in range(len(texts)):
        vec = [0.0] * len(texts)
        vec[i] = 1.0
        vectors.append(vec)
    return vectors


_VALIDATOR_WORDING = [
    "verified section",
    "'verified'",
    "official knowledge chunk",
    "missing required section",
    "draft answer failed validation",
]


def test_app006_greeting_escalates_on_missing_rule_not_validation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", _fake_embed_no_matches)

    case_state = CaseState()
    create_fn = dispatching_create(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("c1", "get_applicant_record", {"applicant_id": "APP-006"})])),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c2",
                            "check_documents",
                            {"applicant_id": "APP-006", "curriculum": "WAEC (West African Senior School Certificate)"},
                        )
                    ]
                )
            ),
            FakeResponse(
                FakeMessage(
                    tool_calls=[
                        FakeToolCall(
                            "c3",
                            "evaluate_eligibility",
                            {"case_state": {"applicant_id": "APP-006", "curriculum": "WAEC (West African Senior School Certificate)"}},
                        )
                    ]
                )
            ),
            # No fourth response needed -- the orchestrator short-circuits with its
            # own plain-language answer the moment evaluate_eligibility signals
            # "no rule chunk" and search_requirements confirms it, never asking the
            # model to draft (or the validator to grade) anything for this turn.
        ]
    )

    answer = run_turn(
        case_state, [], "Hi, my ID is APP-006 -- can you check my equivalency?", client=object(), model="fake-model", create_fn=create_fn
    )

    # 1. search_requirements was actually called this session, before the final answer.
    tool_names = [t.tool for t in case_state.tool_history]
    assert "search_requirements" in tool_names
    assert tool_names.index("search_requirements") > tool_names.index("evaluate_eligibility")
    search_entry = next(t for t in case_state.tool_history if t.tool == "search_requirements")
    assert search_entry.args.get("auto_triggered") == "confirming no_rule_found"

    # 2. Escalation triggered by the missing rule, recorded on CaseState, exactly one ticket.
    assert case_state.facts.get("no_rule_found_reason") == "no_rule_found:WAEC/Nigeria"
    escalate_entries = [t for t in case_state.tool_history if t.tool == "escalate_to_human"]
    assert len(escalate_entries) == 1
    assert escalate_entries[0].args.get("reason") == "no_rule_found"
    assert case_state.facts.get("escalation_ticket_id") is not None

    # 3. The chat reply is plain language -- no validator/section wording.
    lowered = answer.lower()
    for phrase in _VALIDATOR_WORDING:
        assert phrase not in lowered, f"chat reply leaked validator wording: {phrase!r}"
    assert "escalated" in lowered
    assert case_state.facts["escalation_ticket_id"].lower() in lowered or "ticket" in lowered

    # No validation attempt was logged -- this never went through the model-draft/validator path.
    assert "validate_draft" not in tool_names


def test_handle_no_rule_found_defers_when_search_actually_finds_a_match(tmp_path, monkeypatch):
    """If search_requirements's embeddings search finds something the
    keyword-based family match missed, don't escalate on a false alarm --
    flag it as an open question instead and let the normal flow continue."""
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.search_rules._embed", lambda texts: [[1.0] for _ in texts])

    case_state = CaseState(curriculum="WAEC (West African Senior School Certificate)", facts={"country_of_origin": "Nigeria"})
    result = EvaluateEligibilityResult(
        applicant_id="APP-006",
        curriculum="WAEC (West African Senior School Certificate)",
        rule_results=[RuleEvaluation(chunk_id="NONE", topic="curriculum_requirements", label="NONE", verdict="UNDETERMINABLE", reason="no chunk")],
        undeterminable_rule_ids=["NONE"],
    )

    answer = _handle_no_rule_found(case_state, result)

    assert answer is None  # nothing to short-circuit
    assert case_state.facts.get("no_rule_found_reason") is None  # not escalated
    assert any("search_requirements found a possibly relevant chunk" in q for q in case_state.open_questions)
