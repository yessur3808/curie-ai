import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.orchestration.model_service import ResponseCandidate
from agent.tooling import get_runtime_registry


@pytest.mark.parametrize(
    "prompt,expected",
    [
        (
            "Different topic: a fictional dashboard request timed out. No cause was provided. Explain what is known in at most 15 words; no jokes.",
            False,
        ),
        ("Explain what is known in fewer than 20 words.", False),
        ("Tell me what is important in this fictional story in 30 words.", False),
        ("Convert 100 USD to EUR", True),
        ("How many miles in 5 km?", True),
        ("What is 25 celsius in fahrenheit?", True),
        ("50 pounds to kg", True),
    ],
)
def test_conversion_requires_quantity_unit_relationship(monkeypatch, prompt, expected):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    calls = []

    async def blocked(name, params, context):
        calls.append((name, dict(params)))
        raise PermissionError("Sandbox: no conversion execution")

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
                "internal_id": "conversion-boundary",
                "external_user_id": "conversion-boundary",
                "external_chat_id": "conversion-boundary",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == ([("conversion", {"text": prompt})] if expected else [])
    if not expected:
        assert result["text"] == "Understood."
