"""Shared fake Azure OpenAI chat-completion helpers for agent/orchestrator tests."""

from __future__ import annotations

import json

from agent.validation import LLM_VERIFIER_SYSTEM_PROMPT


class FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict):
        self.id = call_id
        self.function = FakeFunction(name, json.dumps(arguments))


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, message: FakeMessage):
        self.message = message


class FakeResponse:
    def __init__(self, message: FakeMessage):
        self.choices = [FakeChoice(message)]


def dispatching_create(main_responses: list[FakeResponse], verifier_response: FakeResponse | None = None):
    """Routes calls to a separate verifier response vs. the main-loop queue, identified by
    which system prompt the call used -- so tests don't have to predict exactly how many
    extra calls the LLM verifier pass will make."""
    main_queue = list(main_responses)
    verifier_response = verifier_response if verifier_response is not None else FakeResponse(FakeMessage(content="PASS"))

    def create(**kwargs):
        if kwargs["messages"][0].get("content") == LLM_VERIFIER_SYSTEM_PROMPT:
            return verifier_response
        return main_queue.pop(0)

    return create
