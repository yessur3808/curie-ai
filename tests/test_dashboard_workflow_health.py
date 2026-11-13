"""Operational health and readiness are distinct in conversation receipts."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.context_tools import DashboardSnapshotTool
from agent.tooling.contracts import CapabilityDefinition


@pytest.mark.parametrize("health,errors", [("healthy", 0), ("degraded", 1)])
@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize(
    "prompt",
    [
        "Please check the dashboard and tell me whether Curie needs attention.",
        "Can you check if Curie is healthy right now?",
        "Please check Curie's dashboard health again.",
        "Could you check the dashboard for anything that needs attention?",
    ],
)
def test_dashboard_health_is_preserved_with_ready_capabilities(
    monkeypatch, health, errors, prompt, ready
):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    snapshot = {
        "overall_status": health,
        "captured_at": "2026-11-01T08:31:00+00:00",
        "readiness": {"workflow": ready},
        "instances": [
            {
                "instance": "curie",
                "health": health,
                "persona": "Curie",
                "activity": "idle",
                "cpu": "0%",
                "memory": "100 MiB",
                "model": "sandbox",
                "connectors": "sandbox",
                "errors": errors,
            }
        ],
    }
    monkeypatch.setattr(
        "services.dashboard_context.dashboard_snapshot", lambda: snapshot
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        assert name == "dashboard_snapshot"
        return await DashboardSnapshotTool().execute(params, context)

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "dashboard-health",
                "external_user_id": "dashboard-health",
                "external_chat_id": "dashboard-health",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == [("dashboard_snapshot", {})]
    assert result["routing"]["selected_capability"] == "dashboard_snapshot"
    assert result["execution_status"] == "completed"
    assert f"Curie dashboard: {health.title()}" in result["text"]
    assert f"Errors: {errors}" in result["text"]
    if ready:
        assert "All reported readiness checks are healthy." in result["text"]
    else:
        assert "Needs attention:" in result["text"]
        assert "workflow" in result["text"]
        assert "All reported readiness checks are healthy." not in result["text"]
    if health == "degraded":
        assert "Curie dashboard: Healthy" not in result["text"]

    # A follow-up must obtain a fresh snapshot, not repeat the previous health.
    next_health = "degraded" if health == "healthy" else "healthy"
    next_errors = 1 if next_health == "degraded" else 0
    snapshot["overall_status"] = next_health
    snapshot["readiness"]["workflow"] = not ready
    snapshot["instances"][0].update(health=next_health, errors=next_errors)
    followup = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "dashboard-health",
                "external_user_id": "dashboard-health",
                "external_chat_id": "dashboard-health",
                "message_id": "2",
                "text": "Please check Curie's dashboard again now.",
            }
        )
    )
    assert calls == [("dashboard_snapshot", {}), ("dashboard_snapshot", {})]
    assert followup["routing"]["selected_capability"] == "dashboard_snapshot"
    assert followup["execution_status"] == "completed"
    assert f"Curie dashboard: {next_health.title()}" in followup["text"]
    assert f"Errors: {next_errors}" in followup["text"]
    assert f"Curie dashboard: {health.title()}" not in followup["text"]
    if ready:
        assert "Needs attention:" in followup["text"]
        assert "workflow" in followup["text"]
        assert "All reported readiness checks are healthy." not in followup["text"]
    else:
        assert "All reported readiness checks are healthy." in followup["text"]
