"""Device reads followed by prohibitions must never turn into mutations."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize(
    "prohibition",
    [
        "Please don't turn it on.",
        "I don't want you to switch the desk lamp on.",
        "Please refrain from changing the desk lamp.",
        "I'd rather you didn't turn the desk lamp on.",
        "Could you please avoid switching the desk lamp on?",
        "Leave the desk lamp as it is; don't switch it on.",
        "Don't change the desk lamp, even if it is off.",
        "Please don't switch the desk lamp on or off; just leave it alone.",
        "Do not turn the desk lamp on, even if I ask whether it is off.",
    ],
)
@pytest.mark.parametrize(
    "followup",
    [
        "Is the desk lamp still off?",
        "Could you check whether the desk lamp is not on?",
        "Please check whether the desk lamp is off. Do not change its state.",
        "Could you see if my desk lamp is off? Please don't change anything.",
        "Would you tell me whether the desk lamp is currently off? Do not change its state.",
    ],
)
def test_state_read_prohibition_and_followup(monkeypatch, prohibition, followup):
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
        assert name == "home_status"
        assert params["target"] == ("bedside lamp" if len(calls) == 3 else "desk lamp")
        return ToolResult(
            f"{params['target']} is off. Sandbox observation.", {}, "sandbox"
        )

    async def reply(*args, **kwargs):
        return ResponseCandidate("Understood; I will leave it unchanged.", "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    monkeypatch.setattr(workflow.model_service, "generate", reply)

    async def scenario():
        prompts = [
            "Can you please see if my desk lamp is off?",
            prohibition,
            followup,
            "Please check whether the bedside lamp is off. Do not change its state.",
        ]
        for index, prompt in enumerate(prompts):
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "device-prohibition",
                    "external_user_id": "device-prohibition",
                    "external_chat_id": "device-prohibition",
                    "message_id": str(index),
                    "text": prompt,
                }
            )
            assert len(calls) == (1 if index < 2 else index)
            if index != 1:
                assert result["routing"]["selected_capability"] == "home_status"
                assert result["execution_status"] in {"completed", "verified"}
        assert all(name == "home_status" for name, _ in calls)
        assert [params["target"] for _, params in calls] == [
            "desk lamp",
            "desk lamp",
            "bedside lamp",
        ]

    asyncio.run(scenario())
