from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from memory import UserManager
from memory.local_store import save_personal_item
from services import research_followup as rf

PROFILE = {"autonomous_followup_enabled": True, "timezone": "Asia/Hong_Kong"}
ORIGIN = {"message_id": "one", "external_chat_id": "42", "external_user_id": "42"}


@pytest.fixture
def queue(monkeypatch):
    rf.set_delivery_channels(["telegram"])
    scheduled, completed = [], []
    monkeypatch.setattr(rf.task_engine, "use_native", lambda: True)
    monkeypatch.setattr(
        rf.task_engine, "upsert_scheduled", lambda doc: scheduled.append(doc) or doc
    )
    monkeypatch.setattr(
        rf.task_engine, "complete_scheduled", lambda *a, **kw: completed.append(kw)
    )
    monkeypatch.setattr(rf.task_engine, "cancel_scheduled", lambda *a: True)
    monkeypatch.setattr(UserManager, "get_user_profile", lambda owner: dict(PROFILE))
    identities = SimpleNamespace(get_external_id=lambda owner, platform: "42")
    monkeypatch.setattr(
        "memory.repositories.get_repositories",
        lambda: SimpleNamespace(identities=identities),
    )
    yield scheduled, completed
    rf.set_delivery_channels(())


def enqueue():
    return rf.queue_research(
        "owner", "telegram", "Research recent battery developments", PROFILE, ORIGIN
    )


def claim(job):
    return {
        "id": "scheduled",
        "owner_id": "owner",
        "payload": {"job_id": job["job_id"]},
        "lease_token": 1,
    }


def test_saves_before_acknowledging_and_replays_idempotently(queue):
    job = enqueue()
    assert enqueue()["job_id"] == job["job_id"]
    assert len(queue[0]) == 1
    assert job["status"] == "queued" and job["budget_minutes"] == 30
    assert "saved the follow-up" in rf.acknowledgement(job, PROFILE)
    assert "HKT" in rf.acknowledgement(job, PROFILE)


@pytest.mark.parametrize(
    "query,minutes",
    [
        ("Research batteries", 30),
        ("Detailed analysis of batteries", 60),
        ("Comprehensive battery report", 120),
    ],
)
def test_deadlines_scale_with_requested_detail(query, minutes):
    assert rf.budget_minutes(query) == minutes


def test_no_background_promise_without_opt_in_native_engine_or_private_destination(
    queue, monkeypatch
):
    assert (
        rf.queue_research("owner", "telegram", "Research batteries", {}, ORIGIN) is None
    )
    assert (
        rf.queue_research(
            "owner",
            "telegram",
            "Research batteries",
            PROFILE,
            {**ORIGIN, "external_chat_id": "-100"},
        )
        is None
    )
    monkeypatch.setattr(rf.task_engine, "use_native", lambda: False)
    assert enqueue() is None


@pytest.mark.parametrize(
    "text",
    [
        "What is my password?",
        "Why did you say that?",
        "Search my inbox",
        "Do not research this",
        "Buy battery shares",
    ],
)
def test_private_or_mutating_requests_do_not_leak_into_public_research(text):
    assert not rf.public_question(text)


def test_unknown_factual_question_can_trigger_research_but_not_personal_context():
    assert rf.needs_research("What causes noctilucent clouds?", "I'm not sure.")
    assert rf.needs_research(
        "Do you know what causes noctilucent clouds?", "I'm not sure."
    )
    assert rf.needs_research(
        "Could you tell me what causes noctilucent clouds?", "I don't know."
    )
    assert not rf.needs_research("Why did you say that?", "I'm not sure.")
    assert not rf.needs_research("What about it?", "I don't know.")


@pytest.mark.asyncio
async def test_success_is_delivered_once_and_survives_worker_restart(queue):
    job = enqueue()
    research = AsyncMock(
        return_value={
            "answer": "Evidence says X [S1].",
            "sources": [{"url": "https://example.org/report"}],
        }
    )
    send = AsyncMock(return_value=True)
    await rf.ResearchFollowupWorker(send, researcher=research)._run_claim(claim(job))
    await rf.ResearchFollowupWorker(send, researcher=research)._run_claim(claim(job))
    assert research.await_count == send.await_count == 1
    assert rf._job("owner", job["job_id"])["status"] == "delivered"
    assert "provisional" in send.call_args.args[2]


@pytest.mark.asyncio
async def test_no_sources_retries_then_reports_honestly_by_deadline(queue):
    job = enqueue()
    send = AsyncMock(return_value=True)
    worker = rf.ResearchFollowupWorker(
        send, researcher=AsyncMock(return_value={"answer": "guess", "sources": []})
    )
    await worker._run_claim(claim(job))
    assert rf._job("owner", job["job_id"])["status"] == "retrying"
    assert queue[1][-1]["success"] is False
    send.assert_not_awaited()
    latest = rf._job("owner", job["job_id"])
    latest["deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    save_personal_item("owner", rf.KIND, latest)
    await worker._run_claim(claim(job))
    assert "couldn’t find enough reliable evidence" in send.call_args.args[2]
    assert (
        "guess" not in send.call_args.args[2] or "don’t want" in send.call_args.args[2]
    )


@pytest.mark.asyncio
async def test_cancellation_during_research_prevents_delivery(queue):
    job = enqueue()

    async def research(query):
        rf.controls("owner", f"/research cancel {job['job_id'][:8]}")
        return {"answer": "answer", "sources": [{"url": "https://example.org"}]}

    send = AsyncMock()
    await rf.ResearchFollowupWorker(send, researcher=research)._run_claim(claim(job))
    send.assert_not_awaited()
    assert rf._job("owner", job["job_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_ambiguous_delivery_is_not_replayed(queue):
    job = enqueue()
    send = AsyncMock(side_effect=TimeoutError)
    research = AsyncMock(
        return_value={"answer": "answer", "sources": [{"url": "https://example.org"}]}
    )
    worker = rf.ResearchFollowupWorker(send, researcher=research)
    await worker._run_claim(claim(job))
    await worker._run_claim(claim(job))
    assert send.await_count == 1
    assert rf._job("owner", job["job_id"])["status"] == "delivery_uncertain"


def test_status_and_cancel_are_owner_scoped(queue):
    job = enqueue()
    assert "no saved" in rf.controls("other", "/research status")
    assert "Use" in rf.controls("other", f"/research cancel {job['job_id'][:8]}")
    assert rf._job("owner", job["job_id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_unexpected_failure_does_not_complete_the_job(queue, monkeypatch):
    job = enqueue()
    monkeypatch.setattr(
        UserManager,
        "get_user_profile",
        lambda owner: (_ for _ in ()).throw(RuntimeError()),
    )
    with pytest.raises(RuntimeError):
        await rf.ResearchFollowupWorker(AsyncMock())._run_claim(claim(job))
    assert queue[1][-1]["success"] is False
