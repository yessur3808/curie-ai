import asyncio

import pytest

from agent.intent_router import (
    TOOL_PARAMETER_SPECS,
    classify_request,
    resolve_request,
    validate_tool_request,
)


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("What is using all my RAM?", "ram_usage"),
        ("Show my computer specs", "hardware"),
        ("Is it raining in Paris?", "weather"),
        ("Research current battery market trends", "research"),
        ("Inspect the files in this project", "inspect_project"),
        ("Create a directory called demo-files", "create_directory"),
        ("Create a Python project called demo-app", "create_python_project"),
        ("Run the tests in this project", "run_tests"),
        ("Fix the authentication bug in this project", "project_change"),
    ],
)
def test_every_registered_intent_has_a_positive_case(text, action):
    assert classify_request(text).action == action


@pytest.mark.parametrize(
    "text",
    [
        "I have a good memory of visiting Paris",
        "That project was fun to talk about",
        "I might make dinner later",
        "The weather metaphor in that book was clever",
        "Market trends are strange lately",
        "My trip to Paris was fun",
        "Hello, how are you?",
    ],
)
def test_ordinary_conversation_is_not_deterministically_routed(text):
    assert classify_request(text) is None


def test_missing_project_name_gets_focused_question():
    request = classify_request("Create a new Python project")
    assert request.action == "clarify"
    assert "name" in request.params["message"].lower()


def test_multiple_explicit_actions_get_clarification():
    request = classify_request("Research current weather in Paris")
    assert request.action == "clarify"
    assert "more than one" in request.params["message"].lower()


def test_all_tool_specs_match_runtime_registry():
    from agent.tooling import get_runtime_registry

    # The unified registry also contains specialists and operational components;
    # every deterministic action spec must be executable, but not vice versa.
    assert set(TOOL_PARAMETER_SPECS) <= set(get_runtime_registry().names())


def test_parameter_schema_applies_defaults_and_rejects_bad_values():
    params, error = validate_tool_request("project_change", {"request": "Fix it"})
    assert error is None
    assert params == {"request": "Fix it", "path": ".", "run_tests": False}
    params, error = validate_tool_request("create_directory", {"name": "../escape"})
    assert params is None
    assert "not valid" in error


def _resolve_with_model(
    monkeypatch, payload, text="Could you investigate fresh chip supply data?"
):
    async def fake_model(*args, **kwargs):
        return payload

    monkeypatch.setattr("agent.intent_router._ask_intent_model", fake_model)
    return asyncio.run(resolve_request(text))


def test_high_confidence_model_route_is_schema_validated(monkeypatch):
    request = _resolve_with_model(
        monkeypatch,
        '{"action":"research","params":{"query":"chip supply"},"confidence":0.91,"clarification":null}',
    )
    assert request.action == "research"
    assert request.source == "model"
    assert request.params == {"query": "chip supply"}


def test_medium_confidence_model_route_falls_back_to_conversation(monkeypatch):
    request = _resolve_with_model(
        monkeypatch,
        '{"action":"research","params":{"query":"chip supply"},"confidence":0.6,"clarification":null}',
    )
    assert request is None


def test_medium_confidence_tool_guess_does_not_turn_correction_into_question(
    monkeypatch,
):
    request = _resolve_with_model(
        monkeypatch,
        '{"action":"inspect_project","params":{},"confidence":0.5,'
        '"clarification":"What project do you mean?"}',
        text="There is no project",
    )
    assert request is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        '{"action":"project_change","params":{"request":"edit"},"confidence":0.99,"clarification":null}',
        '{"action":"research","params":{"query":"chips"},"confidence":0.2,"clarification":null}',
    ],
)
def test_invalid_unsafe_or_low_confidence_model_output_does_not_route(
    monkeypatch, payload
):
    assert _resolve_with_model(monkeypatch, payload) is None


def test_model_route_with_missing_parameter_asks_focused_question(monkeypatch):
    request = _resolve_with_model(
        monkeypatch,
        '{"action":"research","params":{},"confidence":0.9,"clarification":null}',
    )
    assert request.action == "clarify"
    assert "query" in request.params["message"]


def test_non_action_chat_never_calls_intent_model(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("intent model should not be called")

    monkeypatch.setattr("agent.intent_router._ask_intent_model", fail)
    assert asyncio.run(resolve_request("Hello, how are you?")) is None


def test_formatting_request_with_inline_url_never_calls_intent_model(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("intent model should not be called")

    monkeypatch.setattr("agent.intent_router._ask_intent_model", fail)
    text = (
        "Formatting test: write a bold heading, bullets, a table, and an inline "
        "link to https://example.com"
    )
    assert asyncio.run(resolve_request(text)) is None
