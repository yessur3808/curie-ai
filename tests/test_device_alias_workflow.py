"""Explicit alias delimiters must survive the actual workflow unchanged."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize(
    "prompt,alias",
    [
        ('Call the desk lamp "Reading Light".', "Reading Light"),
        ("Please call the desk lamp 'Reading Light'.", "Reading Light"),
        ('Call the desk lamp "Reader\'s Light".', "Reader's Light"),
        ("Please call the desk lamp 'Reading \"Glow\" Light'.", 'Reading "Glow" Light'),
        ('Call the desk lamp "The Glow!".', "The Glow!"),
        ('Call the desk lamp "Glow now".', "Glow now"),
        ('Call the desk lamp "Light in the future".', "Light in the future"),
        ('Call the desk lamp "Light when reading".', "Light when reading"),
        ('Call the desk lamp "Light named Aurora".', "Light named Aurora"),
    ],
)
def test_quoted_multiword_alias_exact_arguments(monkeypatch, prompt, alias):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        return ToolResult("Alias recorded in sandbox only.", {}, "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None

    async def no_generation(*a, **kw):
        raise AssertionError("Explicit alias must not fall back to free generation")

    monkeypatch.setattr(workflow.model_service, "generate", no_generation)
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "alias-sandbox",
                "external_user_id": "alias-sandbox",
                "external_chat_id": "alias-sandbox",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == [("home_alias", {"device": "desk lamp", "alias": alias})]
    assert result["routing"]["selected_capability"] == "home_alias"


@pytest.mark.parametrize(
    "prompt",
    [
        "Call the desk lamp Reading Light.",
        "Please call the desk lamp evening glow.",
        "Call the desk lamp Reading.",
        "Call the desk lamp \"Reading Light'.",
        'Call the desk lamp Reading Light".',
        'Call the desk lamp "Reading Light.',
    ],
)
def test_unquoted_alias_clarifies_then_quoted_retry_executes(monkeypatch, prompt):
    from agent.intent_router import classify_request
    from agent.orchestration.model_service import ResponseCandidate

    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        return ToolResult("Sandbox alias recorded.", {}, "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None

    async def generate(*args, **kwargs):
        return ResponseCandidate(
            "Sandbox fallback must not replace structured clarification.", "sandbox"
        )

    monkeypatch.setattr(workflow.model_service, "generate", generate)

    async def scenario():
        message = {
            "platform": "telegram",
            "internal_id": "alias-clarify",
            "external_user_id": "alias-clarify",
            "external_chat_id": "alias-clarify",
        }
        result = await workflow.process_message(
            {**message, "message_id": "1", "text": prompt}
        )
        assert calls == []
        assert classify_request(prompt).action == "clarify"
        assert "quot" in result["text"].lower()
        await workflow.process_message(
            {
                **message,
                "message_id": "2",
                "text": 'Call the desk lamp "Reading Light".',
            }
        )
        assert calls == [
            ("home_alias", {"device": "desk lamp", "alias": "Reading Light"})
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "prohibition",
    [
        'Please do not call the desk lamp "Evening Glow".',
        "Do not map the desk lamp to Evening Glow.",
        "Please refrain from renaming the desk lamp.",
    ],
)
def test_alias_prohibition_after_success_never_executes(monkeypatch, prohibition):
    from agent.orchestration.model_service import ResponseCandidate

    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        return ToolResult("Sandbox alias recorded.", {}, "sandbox")

    async def generate(*args, **kwargs):
        return ResponseCandidate("I will leave the alias unchanged.", "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    monkeypatch.setattr(workflow.model_service, "generate", generate)

    async def scenario():
        message = {
            "platform": "telegram",
            "internal_id": "alias-prohibition",
            "external_user_id": "alias-prohibition",
            "external_chat_id": "alias-prohibition",
        }
        await workflow.process_message(
            {
                **message,
                "message_id": "1",
                "text": 'Call the desk lamp "Reading Light".',
            }
        )
        assert calls == [
            ("home_alias", {"device": "desk lamp", "alias": "Reading Light"})
        ]
        await workflow.process_message(
            {**message, "message_id": "2", "text": prohibition}
        )
        assert calls == [
            ("home_alias", {"device": "desk lamp", "alias": "Reading Light"})
        ]

    asyncio.run(scenario())
