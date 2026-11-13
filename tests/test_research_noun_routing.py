"""Research used as a noun does not authorize a research action."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.orchestration.model_service import ResponseCandidate
from agent.tooling import get_runtime_registry


@pytest.mark.parametrize(
    "prompt,expected",
    [
        (
            "My fictional research submarine is named Tern. Acknowledge in at most 5 words.",
            None,
        ),
        ("Our research project is called Lantern. Please acknowledge.", None),
        ("My research notebook is blue. Acknowledge.", None),
        ("Research is my favourite part of writing. Just acknowledge.", None),
        ("Research was difficult yesterday. Please acknowledge.", None),
        ("Research has been rewarding lately. Just acknowledge.", None),
        ("Research battery recycling methods.", "research"),
        ("Research whether batteries can be recycled safely.", "research"),
        ("Could you research battery recycling methods?", "research"),
        ("Please research battery recycling methods.", "research"),
    ],
)
@pytest.mark.parametrize("model_enabled", [False, True])
def test_research_noun_versus_request(monkeypatch, prompt, expected, model_enabled):
    monkeypatch.setenv("INTENT_LLM_ENABLED", str(model_enabled).lower())
    classifier_calls = []

    async def unexpected_classifier(*args, **kwargs):
        classifier_calls.append(args)
        raise AssertionError(
            "Unambiguous noun statements and direct requests need no classifier"
        )

    monkeypatch.setattr("agent.intent_router._ask_intent_model", unexpected_classifier)
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    calls = []

    async def blocked(name, params, context):
        calls.append((name, dict(params)))
        raise PermissionError("Sandbox: no real research")

    async def generate(*args, **kwargs):
        return ResponseCandidate("Understood.", "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    monkeypatch.setattr(workflow.model_service, "generate", generate)
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "research-noun-test",
                "external_user_id": "research-noun-test",
                "external_chat_id": "research-noun-test",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == ([("research", {"query": prompt})] if expected else [])
    assert classifier_calls == []
    if expected is None:
        assert result["text"] == "Understood."
