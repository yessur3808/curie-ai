"""In-process REST serialization with actual workflow and blocked executors."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Please check Curie's dashboard.", "dashboard_snapshot"),
        ("Please check my calendar for tomorrow.", "calendar_list"),
        ("Can you currently access the dashboard?", None),
        ("Do you currently have access to my calendar?", None),
    ],
)
def test_rest_chat_does_not_serialize_internal_diagnostics(
    monkeypatch, prompt, expected
):
    # Import must not start the connector's background voice-cleanup worker.
    with patch("threading.Thread.start"):
        from connectors import api

    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda _: (False, "Synthetic diagnostic /private/CASE0701"),
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "Asia/Hong_Kong"},
    )
    monkeypatch.setattr(api, "get_internal_id", lambda *_: "rest-privacy-owner")
    # Admission coordination is outside this serialization test and must not
    # touch process-global native request state.
    monkeypatch.setattr(api, "begin_request", lambda *_: None)
    calls = []

    async def blocked(*args, **kwargs):
        calls.append(True)
        raise AssertionError("No tool may execute")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    actual_process = workflow.process_message
    results = []

    async def observed(message):
        result = await actual_process(message)
        results.append(result)
        return result

    monkeypatch.setattr(workflow, "process_message", observed)
    monkeypatch.setattr(api, "_workflow", workflow)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/chat", json={"user_id": "rest-privacy-owner", "message": prompt}
            )
        assert response.status_code == 200
        assert calls == []
        assert len(results) == 1
        if expected:
            assert results[0]["routing"]["selected_capability"] == expected
            assert results[0]["execution_status"] == "failed"
        payload = response.json()
        assert set(payload) == {
            "text",
            "timestamp",
            "model_used",
            "processing_time_ms",
            "voice_url",
        }
        assert "CASE0701" not in response.text
        assert "unavailable" in payload["text"].casefold()
        assert payload["voice_url"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Please check Curie's dashboard.", "dashboard_snapshot"),
        ("Please check my calendar for tomorrow.", "calendar_list"),
    ],
)
def test_websocket_privacy_and_owner_switch_denial(monkeypatch, prompt, expected):
    from fastapi.testclient import TestClient

    with patch("threading.Thread.start"):
        from connectors import api

    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition,
        "availability",
        lambda _: (False, "Synthetic /private/CASE0716"),
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "Asia/Hong_Kong"},
    )
    monkeypatch.setattr(api, "get_internal_id", lambda owner, *args: owner)
    monkeypatch.setattr(api, "voice_session_manager", lambda: None)
    monkeypatch.setattr(api, "active_connections", [])
    calls, inputs, results = [], [], []

    async def blocked(*args, **kwargs):
        calls.append(True)
        raise AssertionError("No tool may execute")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    actual_process = workflow.process_message

    async def observed(message):
        inputs.append(message)
        result = await actual_process(message)
        results.append(result)
        return result

    monkeypatch.setattr(workflow, "process_message", observed)
    monkeypatch.setattr(api, "_workflow", workflow)
    with TestClient(api.app) as client:
        with client.websocket_connect("/ws/chat") as socket:
            socket.send_json({"user_id": "socket-owner", "message": prompt})
            response = socket.receive_json()
            assert set(response) == {
                "text",
                "timestamp",
                "model_used",
                "processing_time_ms",
                "session_id",
            }
            assert "CASE0716" not in json.dumps(response)
            assert "unavailable" in response["text"].casefold()
            assert results[0]["routing"]["selected_capability"] == expected
            assert results[0]["execution_status"] == "failed"
            socket.send_json({"user_id": "different-owner", "message": prompt})
            assert socket.receive_json() == {
                "error": "A live session cannot change users"
            }
            assert len(inputs) == 1
            # Denial must not break the original owner's subsequent request.
            socket.send_json(
                {
                    "user_id": "socket-owner",
                    "message": "Can you currently access the dashboard?",
                }
            )
            followup = socket.receive_json()
            assert "unavailable" in followup["text"].casefold()
            assert "CASE0716" not in json.dumps(followup)
            assert len(inputs) == 2
            assert all(item["internal_id"] == "socket-owner" for item in inputs)
    assert calls == []
    assert api.active_connections == []
