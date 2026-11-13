import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition


@pytest.mark.parametrize(
    "prompt",
    [
        "Please check whether my desk lamp is off, without changing its state.",
        "Check if the desk lamp is on without changing anything.",
    ],
)
def test_read_only_constraint_is_not_device_name(monkeypatch, prompt):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda self: (True, "sandbox")
    )
    calls = []

    async def blocked(name, params, context):
        calls.append((name, dict(params)))
        raise PermissionError("Sandbox: no device access")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "state-without-change",
                "external_user_id": "state-without-change",
                "external_chat_id": "state-without-change",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == [("home_status", {"target": "desk lamp", "provider": ""})]
