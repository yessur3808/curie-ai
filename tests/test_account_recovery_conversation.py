"""An unavailable account must not leave stale failure state after recovery."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize("ready", ["gmail_search", "gmail_send"])
@pytest.mark.parametrize(
    "prompt",
    [
        "Do you still have access to Gmail today?",
        "Can you currently access my Gmail?",
        "Could you use Gmail right now?",
    ],
)
def test_gmail_partial_availability_is_explicit(monkeypatch, ready, prompt):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda item: (item.name == ready, "Synthetic CASE0931"),
    )
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Capability inspection must not access accounts")

    monkeypatch.setattr(get_runtime_registry(), "execute", forbidden)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    monkeypatch.setattr(workflow.model_service, "generate", forbidden)
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "partial-gmail",
                "external_user_id": "partial-gmail",
                "external_chat_id": "partial-gmail",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == []
    assert "CASE0931" not in result["text"]
    text = result["text"].casefold()
    assert (
        f"- **read/search:** {'available' if ready == 'gmail_search' else 'unavailable'}"
        in text
    )
    assert (
        f"- **send:** {'available' if ready == 'gmail_send' else 'unavailable'}" in text
    )
    assert "does not prove your account connection" in text
    assert "authorization and approval" in text

    # A connection can lose one permission and gain another during a conversation.
    # Recheck metadata without treating earlier readiness as proof of access.
    ready = "gmail_send" if ready == "gmail_search" else "gmail_search"
    followup = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "partial-gmail",
                "external_user_id": "partial-gmail",
                "external_chat_id": "partial-gmail",
                "message_id": "2",
                "text": "Can you currently access my Gmail?",
            }
        )
    )
    assert calls == []
    assert "CASE0931" not in followup["text"]
    text = followup["text"].casefold()
    assert (
        f"- **read/search:** {'available' if ready == 'gmail_search' else 'unavailable'}"
        in text
    )
    assert (
        f"- **send:** {'available' if ready == 'gmail_send' else 'unavailable'}" in text
    )
    assert "does not prove your account connection" in text
    assert "authorization and approval" in text


@pytest.mark.parametrize(
    "prompt",
    [
        "Do you currently have access to my Gmail?",
        "Could you still read Gmail for me?",
        "Can you use my Gmail right now?",
    ],
)
@pytest.mark.parametrize("available", [False, True])
def test_gmail_capability_variants_are_non_executing(monkeypatch, prompt, available):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda item: (
            available and item.name in {"gmail_search", "gmail_send"},
            "Synthetic CASE0916",
        ),
    )
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Capability questions must not execute or generate")

    monkeypatch.setattr(get_runtime_registry(), "execute", forbidden)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    monkeypatch.setattr(workflow.model_service, "generate", forbidden)
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "gmail-capability-variants",
                "external_user_id": "gmail-capability-variants",
                "external_chat_id": "gmail-capability-variants",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == []
    assert "CASE0916" not in result["text"]
    if available:
        assert "does not prove your account connection" in result["text"]
        assert "authorization and approval" in result["text"]
    else:
        assert "unavailable" in result["text"].casefold()


@pytest.mark.parametrize(
    "capability,prompts",
    [
        (
            "gmail_search",
            [
                "Please find emails about dinner.",
                "Can you access Gmail?",
                "Please find emails about the invoice.",
            ],
        ),
        (
            "calendar_list",
            [
                "Please check my calendar for tomorrow.",
                "Can you access my calendar?",
                "Please check my calendar for today.",
            ],
        ),
    ],
)
def test_account_recovery_is_rechecked_without_stale_results(
    monkeypatch, capability, prompts
):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "Asia/Hong_Kong"},
    )
    available = False
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda item: (
            available and item.name == capability,
            "Synthetic diagnostic CASE0901",
        ),
    )
    calls = []
    registry = get_runtime_registry()

    async def execute(params, context):
        calls.append(dict(params))
        return ToolResult("Sandbox recovered account result.", {}, "sandbox")

    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected account executor")

    for definition in registry.all():
        monkeypatch.setattr(
            definition.executor,
            "execute",
            execute if definition.name == capability else forbidden,
        )
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    monkeypatch.setattr(workflow.model_service, "generate", forbidden)

    async def scenario():
        nonlocal available
        for index, prompt in enumerate(prompts):
            if index == 2:
                available = True
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "account-recovery",
                    "external_user_id": "account-recovery",
                    "external_chat_id": "account-recovery",
                    "message_id": str(index),
                    "text": prompt,
                }
            )
            assert "CASE0901" not in result["text"]
            if index < 2:
                assert calls == []
                assert "unavailable" in result["text"].casefold()
                assert "Sandbox recovered" not in result["text"]
                if index == 0:
                    assert result["routing"]["selected_capability"] == capability
                    assert result["execution_status"] == "failed"
            else:
                assert result["routing"]["selected_capability"] == capability
                assert result["execution_status"] == "completed"
                assert len(calls) == 1
                assert "Sandbox recovered account result." in result["text"]
                if capability == "gmail_search":
                    assert calls[0] == {"query": prompt}
                else:
                    assert set(calls[0]) >= {"time_min", "time_max"}
                    assert all(
                        calls[0][key].endswith("+08:00")
                        for key in ("time_min", "time_max")
                    )

        answer = await workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "account-recovery",
                "external_user_id": "account-recovery",
                "external_chat_id": "account-recovery",
                "message_id": "3",
                "text": prompts[1],
            }
        )
        assert len(calls) == 1
        assert "tools are available" in answer["text"]
        assert "does not prove your account connection" in answer["text"]

        # A second outage must not reuse a successful result or stale availability.
        available = False
        fresh_request = (
            "Please find emails about the receipt."
            if capability == "gmail_search"
            else "Please check my calendar for next week."
        )
        for index, prompt in enumerate((fresh_request, prompts[1]), start=4):
            denied = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "account-recovery",
                    "external_user_id": "account-recovery",
                    "external_chat_id": "account-recovery",
                    "message_id": str(index),
                    "text": prompt,
                }
            )
            assert len(calls) == 1
            assert "unavailable" in denied["text"].casefold()
            assert "Sandbox recovered" not in denied["text"]
            assert "CASE0901" not in denied["text"]
            if index == 4:
                assert denied["execution_status"] == "failed"
                assert denied["routing"]["selected_capability"] == capability
                if capability == "gmail_search":
                    assert denied["routing"]["parameters"] == {"query": fresh_request}

    asyncio.run(scenario())
