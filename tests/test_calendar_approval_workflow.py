"""Multi-turn approval lifecycle with a sandbox calendar executor."""

import asyncio
import json
import re
import sqlite3

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize("fault", ["expired", "parameters", "action"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_calendar_approval_denies_expired_or_changed_drafts(
    monkeypatch, fault, decision
):
    from memory import local_store

    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "Asia/Hong_Kong"},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        return ToolResult("Sandbox event created.", {}, "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def turn(text, index):
        return await workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "expiry-cycle",
                "external_user_id": "expiry-cycle",
                "external_chat_id": "expiry-cycle",
                "message_id": str(index),
                "text": text,
            }
        )

    async def scenario():
        draft = await turn(
            "Create a planning meeting tomorrow at 11 am on my calendar", 1
        )
        assert draft["routing"]["selected_capability"] == "calendar_create"
        assert draft["routing"]["approval_required"]
        assert calls == []
        token = re.search(r"/approve action ([a-f0-9]{8})", draft["text"]).group(1)
        # Fault injection is restricted to the autouse fixture's temporary DB.
        with sqlite3.connect(local_store._PATH) as conn:
            if fault == "expired":
                conn.execute(
                    "UPDATE pending_actions SET expires_at=? WHERE token=?",
                    ("2000-01-01T00:00:00+00:00", token),
                )
            else:
                row = conn.execute(
                    "SELECT action_json FROM pending_actions WHERE token=?", (token,)
                ).fetchone()
                pending = json.loads(row[0])
                if fault == "parameters":
                    pending["params"]["summary"] = "Altered appointment"
                else:
                    pending["action"] = "calendar_delete"
                conn.execute(
                    "UPDATE pending_actions SET action_json=? WHERE token=?",
                    (json.dumps(pending), token),
                )
        for index, command in enumerate((decision, "approve"), start=2):
            denied = await turn(f"/{command} action {token}", index)
            assert denied["execution_status"] == "failed"
            assert (
                denied["execution"]["outcomes"][0]["error_type"] == "invalid_approval"
            )
            assert calls == []
            assert "event created" not in denied["text"].casefold()

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("outcome", ["success", "failed", "timeout"])
@pytest.mark.parametrize(
    "prompt",
    [
        "Schedule a dentist appointment tomorrow at 9:30 am on my calendar",
        "Create a meeting tomorrow at 10 am on my calendar",
    ],
)
def test_calendar_approval_is_required_and_single_use(
    monkeypatch, decision, outcome, prompt
):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "Asia/Hong_Kong"},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params), context.approved))
        if outcome == "timeout":
            raise TimeoutError("Sandbox calendar timeout")
        if outcome == "failed":
            return ToolResult(
                "Sandbox calendar creation failed.",
                {"verification_status": "failed"},
                "sandbox",
            )
        return ToolResult("Sandbox event created.", {"id": "sandbox-event"}, "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def turn(text, index):
        return await workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "approval-cycle",
                "external_user_id": "approval-cycle",
                "external_chat_id": "approval-cycle",
                "message_id": str(index),
                "text": text,
            }
        )

    async def scenario():
        draft = await turn(prompt, 1)
        assert calls == []
        assert draft["routing"]["approval_required"]
        token = re.search(r"/approve action ([a-f0-9]{8})", draft["text"])
        assert token
        token = token.group(1)
        result = await turn(f"/{decision} action {token}", 2)
        if decision == "approve":
            assert result["execution_status"] == (
                "completed" if outcome == "success" else "failed"
            )
            assert len(calls) == 1
            assert calls[0][0] == "calendar_create"
            assert calls[0][1] == draft["routing"]["parameters"]
            assert calls[0][2] is True
            if outcome != "success":
                assert "event created" not in result["text"].casefold()
        else:
            # The plan aggregates rejected steps as failed; retain the specific
            # rejection outcome and verify that no calendar execution occurred.
            assert result["execution_status"] == "failed"
            assert result["execution"]["outcomes"][0]["status"] == "rejected"
            assert "No changes were made" in result["text"]
            assert calls == []
        count = len(calls)
        replay = await turn(f"/approve action {token}", 3)
        assert len(calls) == count
        assert replay["execution_status"] == "failed"

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["owner", "connector"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_calendar_approval_cannot_cross_identity_boundary(
    monkeypatch, mismatch, decision
):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "Asia/Hong_Kong"},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params), context.internal_id, context.platform))
        return ToolResult("Sandbox event created.", {"id": "sandbox-event"}, "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def turn(text, index, owner="owner-a", platform="telegram"):
        return await workflow.process_message(
            {
                "platform": platform,
                "internal_id": owner,
                "external_user_id": owner,
                "external_chat_id": owner,
                "message_id": str(index),
                "text": text,
            }
        )

    async def scenario():
        draft = await turn(
            "Schedule a dentist appointment tomorrow at 9:30 am on my calendar", 1
        )
        assert calls == []
        token = re.search(r"/approve action ([a-f0-9]{8})", draft["text"]).group(1)
        denied = await turn(
            f"/{decision} action {token}",
            2,
            owner="owner-b" if mismatch == "owner" else "owner-a",
            platform="api" if mismatch == "connector" else "telegram",
        )
        assert denied["execution_status"] == "failed"
        assert calls == []
        assert "dentist" not in denied["text"].casefold()
        # An invalid identity/connector must not consume the rightful token.
        approved = await turn(f"/approve action {token}", 3)
        assert approved["execution_status"] == "completed"
        assert len(calls) == 1
        assert calls[0][0] == "calendar_create"
        assert calls[0][1] == draft["routing"]["parameters"]
        assert calls[0][2:] == ("owner-a", "telegram")

    asyncio.run(scenario())
