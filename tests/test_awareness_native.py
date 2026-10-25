"""Real Rust scheduler recovery and owner isolation, no network or delivery."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("_curie_task_engine")

from agent import task_engine
from memory import UserManager
from memory.local_store import save_personal_item
from services import research_followup as rf


@pytest.mark.asyncio
async def test_native_scheduled_followup_survives_restart_and_cannot_be_double_claimed(
    monkeypatch,
):
    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    profile = {"autonomous_followup_enabled": True}
    monkeypatch.setattr(UserManager, "get_user_profile", lambda owner: profile)
    from memory.repositories import get_repositories

    owner = get_repositories().identities.get_or_create("telegram", "test-42")
    rf.set_delivery_channels(["telegram"])
    try:
        job = rf.queue_research(
            owner,
            "telegram",
            "Research battery technology",
            profile,
            {
                "message_id": "one",
                "external_chat_id": "test-42",
                "external_user_id": "test-42",
            },
        )
        assert job is not None
        claim = task_engine.claim_scheduled(
            worker_id="one", kind=rf.KIND, lease_ms=1000
        )
        assert len(claim) == 1
        assert task_engine.claim_scheduled(worker_id="two", kind=rf.KIND) == []
        task_engine.complete_scheduled(
            claim[0]["id"],
            worker_id="one",
            lease_token=claim[0]["lease_token"],
            success=False,
        )
        # Simulate a resumed worker at deadline, avoiding a real-time wait.
        job["deadline"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        save_personal_item(owner, rf.KIND, job)
        send = AsyncMock(return_value=True)
        worker = rf.ResearchFollowupWorker(send, researcher=AsyncMock())
        future = datetime.now(timezone.utc) + timedelta(minutes=1)
        monkeypatch.setattr(
            task_engine,
            "_now_values",
            lambda: (int(future.timestamp() * 1000), future.isoformat()),
        )
        resumed = task_engine.claim_scheduled(worker_id=worker.worker_id, kind=rf.KIND)
        assert len(resumed) == 1
        assert resumed[0]["lease_token"] > claim[0]["lease_token"]
        await worker._run_claim(resumed[0])
        assert send.await_count == 1
        await worker._run_claim(resumed[0])
        assert send.await_count == 1
    finally:
        rf.set_delivery_channels(())


@pytest.mark.asyncio
async def test_operational_chat_path_enqueues_before_ack_and_status_bypasses_research(
    monkeypatch,
):
    from agent.chat_workflow import ChatWorkflow

    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    owner = UserManager.get_or_create_user_internal_id(
        channel="telegram", external_id="test-43"
    )
    UserManager.update_user_profile(owner, {"autonomous_followup_enabled": True})
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    monkeypatch.setattr(workflow.learning_service, "submit", lambda *a, **k: None)
    message = {
        "platform": "telegram",
        "internal_id": owner,
        "external_user_id": "test-43",
        "external_chat_id": "test-43",
        "message_id": "research-one",
        "text": "Research a detailed comparison of sodium-ion and lithium-ion batteries. Get back to me.",
        "timestamp": datetime.now(timezone.utc),
    }
    rf.set_delivery_channels(["telegram"])
    try:
        response = await workflow.process_message(message)
        assert response["execution_status"] == "queued"
        assert "saved the follow-up" in response["text"]
        status = await workflow.process_message(
            {**message, "message_id": "research-status", "text": "/research status"}
        )
        assert "Research follow-ups:" in status["text"]
        assert "queued" in status["text"]
        claims = task_engine.claim_scheduled(worker_id="regression", kind=rf.KIND)
        assert len(claims) == 1
    finally:
        rf.set_delivery_channels(())
