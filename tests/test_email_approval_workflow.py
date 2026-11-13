"""Email approval lifecycle; classifier and send executor are sandboxed."""

import asyncio
import json
import re
import sqlite3

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("outcome", ["success", "timeout"])
@pytest.mark.parametrize("mismatch", [None, "owner", "connector"])
@pytest.mark.parametrize(
    "prohibition",
    [
        "Please don't send any email yet.",
        "Please hold off on sending any email for now.",
    ],
)
def test_email_requires_single_use_approval(
    monkeypatch, decision, outcome, mismatch, prohibition
):
    params = {
        "recipient": "recipient@example.invalid",
        "subject": "Curie test",
        "body": "This is a sandbox test.",
    }
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )

    async def classifier(prompt):
        return json.dumps(
            {
                "intent": "external_messaging",
                "confidence": 0.99,
                "candidate_capability": "gmail_send",
                "entities": params,
            }
        )

    monkeypatch.setattr("agent.intent_router._ask_intent_model", classifier)
    calls = []

    async def execute(name, arguments, context):
        calls.append((name, dict(arguments), context.approved))
        assert name == "gmail_send"
        if outcome == "timeout":
            raise TimeoutError("Sandbox send timeout")
        return ToolResult("Sandbox email sent.", {"id": "sandbox-message"}, "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected general generation")

    monkeypatch.setattr(workflow.model_service, "generate", forbidden)

    async def turn(text, number, owner="email-sandbox", platform="telegram"):
        return await workflow.process_message(
            {
                "platform": platform,
                "internal_id": owner,
                "external_user_id": owner,
                "external_chat_id": owner,
                "message_id": str(number),
                "text": text,
            }
        )

    async def scenario():
        # Natural model routing intentionally excludes mutations. Verify that
        # a model-selected send cannot bypass that boundary before using the
        # supported explicit command to exercise the approval lifecycle.
        from agent.intent_router import resolve_request

        natural = await resolve_request(
            'Please send an email to recipient@example.invalid with subject "Curie test" and body "This is a sandbox test."'
        )
        assert natural is None
        assert calls == []
        from agent.orchestration.model_service import ResponseCandidate

        async def acknowledgment(*args, **kwargs):
            return ResponseCandidate("No email will be sent.", "sandbox")

        monkeypatch.setattr(workflow.model_service, "generate", acknowledgment)
        await turn(prohibition, "prohibition")
        assert calls == []
        monkeypatch.setattr(workflow.model_service, "generate", forbidden)
        draft = await turn(
            "/gmail send recipient@example.invalid | Curie test | This is a sandbox test.",
            1,
        )
        assert calls == []
        assert draft["routing"]["selected_capability"] == "gmail_send"
        assert draft["routing"]["parameters"] == params
        assert draft["routing"]["approval_required"]
        token = re.search(r"/approve action ([a-f0-9]{8})", draft["text"]).group(1)
        # Positive feedback about a draft is not approval to send it.
        monkeypatch.setattr(workflow.model_service, "generate", acknowledgment)
        await turn("That draft looks fine, but don't send it yet.", "draft-feedback")
        assert calls == []
        monkeypatch.setattr(workflow.model_service, "generate", forbidden)
        if mismatch:
            denied = await turn(
                f"/{decision} action {token}",
                "intruder",
                owner="other-owner" if mismatch == "owner" else "email-sandbox",
                platform="api" if mismatch == "connector" else "telegram",
            )
            assert denied["execution_status"] == "failed"
            assert calls == []
            assert params["recipient"] not in denied["text"]
            assert params["body"] not in denied["text"]
            # Invalid identity must not consume or reveal the rightful draft.
        result = await turn(f"/{decision} action {token}", 2)
        if decision == "approve":
            assert calls == [("gmail_send", params, True)]
            assert result["execution_status"] == (
                "completed" if outcome == "success" else "failed"
            )
            if outcome == "timeout":
                assert "email sent" not in result["text"].casefold()
        else:
            assert calls == []
            assert result["execution"]["outcomes"][0]["status"] == "rejected"
        before = len(calls)
        replay = await turn(f"/approve action {token}", 3)
        assert replay["execution_status"] == "failed"
        assert len(calls) == before

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["expired", "recipient", "body", "action"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_email_approval_denies_expired_or_changed_drafts(monkeypatch, fault, decision):
    from memory import local_store

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
        raise AssertionError("Invalid approval must never reach any executor")

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected general generation")

    monkeypatch.setattr(workflow.model_service, "generate", forbidden)

    async def turn(text, index):
        return await workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "email-fault-sandbox",
                "external_user_id": "email-fault-sandbox",
                "external_chat_id": "email-fault-sandbox",
                "message_id": str(index),
                "text": text,
            }
        )

    async def scenario():
        draft = await turn(
            "/gmail send recipient@example.invalid | Sandbox approval | Do not deliver externally.",
            1,
        )
        assert calls == []
        assert draft["routing"]["selected_capability"] == "gmail_send"
        assert draft["routing"]["parameters"] == {
            "recipient": "recipient@example.invalid",
            "subject": "Sandbox approval",
            "body": "Do not deliver externally.",
        }
        assert draft["routing"]["approval_required"]
        token = re.search(r"/approve action ([a-f0-9]{8})", draft["text"]).group(1)
        # Only the autouse fixture's temporary database is modified.
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
                if fault == "action":
                    pending["action"] = "calendar_delete"
                else:
                    pending["params"][fault] = (
                        "changed@example.invalid"
                        if fault == "recipient"
                        else "Altered body"
                    )
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
            assert "email sent" not in denied["text"].casefold()

    asyncio.run(scenario())
