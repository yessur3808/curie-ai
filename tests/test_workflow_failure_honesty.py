"""Exercise failure receipts through the real conversation workflow."""

import asyncio
import json

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize(
    "prompt", ["Can you access the dashboard?", "Can you access my calendar?"]
)
def test_capability_question_does_not_echo_internal_diagnostics(monkeypatch, prompt):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda _: (False, "Internal diagnostic /synthetic/private/CASE0646"),
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Capability question must not execute tools")

    monkeypatch.setattr(get_runtime_registry(), "execute", forbidden)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "api",
                "internal_id": "summary-privacy",
                "external_user_id": "summary-privacy",
                "external_chat_id": "summary-privacy",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == []
    assert "CASE0646" not in result["text"]
    assert (
        "unavailable" in result["text"].casefold() or "not" in result["text"].casefold()
    )


@pytest.mark.parametrize(
    "failure", ["timeout", "permission", "failed_receipt", "contradicted_receipt"]
)
@pytest.mark.parametrize(
    "prompt",
    [
        "Could you check Curie's dashboard?",
        "Please check the dashboard for me right now.",
        "Can you check whether Curie is running properly?",
        "Please check the dashboard and keep the result factual.",
    ],
)
def test_dashboard_failure_is_never_completed(monkeypatch, failure, prompt):
    calls = []
    recovered = False

    async def execute(name, params, context):
        calls.append((name, params))
        assert name == "dashboard_snapshot"
        if recovered:
            return ToolResult("Sandbox dashboard recovered: healthy.", {}, "sandbox")
        if failure == "timeout":
            raise TimeoutError("Sandbox dashboard timed out")
        if failure == "permission":
            raise PermissionError("Sandbox dashboard access denied")
        return ToolResult(
            "The dashboard could not be checked.",
            {
                "verification_status": (
                    "contradicted" if failure == "contradicted_receipt" else "failed"
                )
            },
            "sandbox failure",
        )

    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(CapabilityDefinition, "availability", lambda _: (True, "test"))
    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def no_generated_embellishment(*args, **kwargs):
        raise AssertionError(
            "Tool failure and recovery receipts must not use creative generation"
        )

    monkeypatch.setattr(workflow.model_service, "generate", no_generated_embellishment)
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "failure-test",
                "external_user_id": "failure-test",
                "external_chat_id": "failure-test",
                "message_id": failure,
                "text": prompt,
            }
        )
    )
    # Read-only timeouts are retried once by the existing workflow policy.
    assert len(calls) == (2 if failure == "timeout" else 1)
    assert calls[0][0] == "dashboard_snapshot"
    assert all(params == {} for _, params in calls)
    assert result["execution_status"] == "failed"
    assert result["text"]
    assert "healthy" not in result["text"].casefold()

    recovered = True
    before = len(calls)
    retry = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "failure-test",
                "external_user_id": "failure-test",
                "external_chat_id": "failure-test",
                "message_id": failure + "-recovered",
                "text": "Please check the dashboard once more now.",
            }
        )
    )
    assert len(calls) == before + 1
    assert calls[-1] == ("dashboard_snapshot", {})
    assert retry["routing"]["selected_capability"] == "dashboard_snapshot"
    assert retry["execution_status"] == "completed"
    assert "Sandbox dashboard recovered: healthy." in retry["text"]
    assert "could not be checked" not in retry["text"].casefold()

    # A subsequent outage must not reuse the successful recovery receipt.
    recovered = False
    before = len(calls)
    relapse = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "failure-test",
                "external_user_id": "failure-test",
                "external_chat_id": "failure-test",
                "message_id": failure + "-relapse",
                "text": "Please check Curie's dashboard again; report only what you can verify.",
            }
        )
    )
    assert len(calls) == before + (2 if failure == "timeout" else 1)
    assert all(call == ("dashboard_snapshot", {}) for call in calls[before:])
    assert relapse["routing"]["selected_capability"] == "dashboard_snapshot"
    assert relapse["execution_status"] == "failed"
    assert "healthy" not in relapse["text"].casefold()
    assert "recovered" not in relapse["text"].casefold()


@pytest.mark.parametrize(
    "prompt,capability",
    [
        ("Could you check Curie's dashboard?", "dashboard_snapshot"),
        ("Check Curie's dashboard.", "dashboard_snapshot"),
        ("Please find emails about dinner.", "gmail_search"),
        ("Could you look in Gmail for messages about dinner?", "gmail_search"),
        ("Please check my calendar for tomorrow.", "calendar_list"),
        ("Could you take a look at how Curie is doing?", "dashboard_snapshot"),
    ],
)
@pytest.mark.parametrize("disappears", [False, True])
@pytest.mark.parametrize(
    "reason",
    [
        "Sandbox unavailable",
        "Internal diagnostic at /synthetic/private/account-cache/CASE0631",
    ],
)
def test_unavailable_tool_never_reaches_executor(
    monkeypatch, prompt, capability, disappears, reason
):
    registry = get_runtime_registry()
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Unavailable tool must not execute")

    model_routed = prompt.startswith("Could you look in Gmail")
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true" if model_routed else "false")

    async def classifier(_prompt):
        return json.dumps(
            {
                "intent": "external_account_read",
                "confidence": 0.95,
                "candidate_capability": "gmail_search",
                "entities": {"query": "dinner"},
            }
        )

    monkeypatch.setattr("agent.intent_router._ask_intent_model", classifier)
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda _: (disappears, reason),
    )
    real_execute = registry.execute
    attempts = []

    async def guarded_execute(name, params, context):
        attempts.append((name, dict(params)))
        assert name == capability
        if disappears:
            monkeypatch.setattr(
                CapabilityDefinition,
                "availability",
                lambda _: (False, reason),
            )
        return await real_execute(name, params, context)

    monkeypatch.setattr(registry, "execute", guarded_execute)
    for definition in registry.all():
        monkeypatch.setattr(definition.executor, "execute", forbidden)
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "unavailable-test",
                "external_user_id": "unavailable-test",
                "external_chat_id": "unavailable-test",
                "message_id": "unavailable",
                "text": prompt,
            }
        )
    )
    assert calls == []
    assert result["routing"]["selected_capability"] == capability
    # Model-routed requests reach the registry availability guard; deterministic
    # requests can be stopped by preflight. Neither may reach the executor.
    assert len(attempts) == (1 if disappears or model_routed else 0)
    if disappears and capability == "gmail_search":
        assert attempts[0][1] == {"query": "dinner" if model_routed else prompt}
    if model_routed:
        assert result["routing"]["source"] == "model"
    if disappears and capability == "calendar_list":
        assert all(key in attempts[0][1] for key in ("time_min", "time_max"))
    assert result["execution_status"] == "failed"
    assert "healthy" not in result["text"].casefold()
    assert "unavailable" in result["text"].casefold()
    assert "internal error" not in result["text"].casefold()
    assert "CASE0631" not in result["text"]
