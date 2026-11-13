import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.orchestration.model_service import ResponseCandidate
from agent.tooling import get_runtime_registry


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Our fictional pottery class has six students. Acknowledge.", False),
        ("Correction: the pottery class budget is 108 dollars. Acknowledge.", False),
        ("Back to the pottery class: give the student count and budget.", False),
        ("Explain a Python class.", True),
        ("Define a class named Rectangle.", True),
    ],
)
def test_class_noun_is_not_alone_a_coding_request(monkeypatch, prompt, expected):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    calls = []

    async def blocked(name, params, context):
        calls.append((name, dict(params)))
        raise PermissionError("Sandbox: no coding execution")

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
                "internal_id": "class-boundary",
                "external_user_id": "class-boundary",
                "external_chat_id": "class-boundary",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == ([("coding_skill", {"text": prompt})] if expected else [])
    if not expected:
        assert result["text"] == "Understood."
