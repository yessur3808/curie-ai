"""Blocking atomicity and crash-recovery tests for the Rust task engine."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import pytest

native = pytest.importorskip("_curie_task_engine")

from agent import task_engine
from agent.task_runtime import DurableTaskRuntime, create_task
from agent.tooling import ToolRegistry, ToolResult
from agent.tooling.registry import definition
from memory import local_store

pytestmark = [pytest.mark.integration, pytest.mark.security]


class DelayedRead:
    name = "native-delayed-read"
    read_only = True

    def __init__(self):
        self.calls = 0

    async def execute(self, params, context):
        self.calls += 1
        await asyncio.sleep(0.05)
        return ToolResult(
            "read completed", {"owner": context.internal_id}, "native-test"
        )


@pytest.fixture()
def native_registry(monkeypatch):
    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    tool = DelayedRead()
    registry = ToolRegistry([definition(tool)])
    monkeypatch.setattr("agent.task_runtime.get_runtime_registry", lambda: registry)
    return tool


def test_native_status_and_stable_hash_parity(monkeypatch):
    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    status = task_engine.task_engine_status()
    assert status["active"] == "rust"
    assert status["fallback"] is False
    payload = {"z": [3, 2, 1], "a": {"desired": "on"}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert (
        task_engine.hash_payload(payload)
        == hashlib.sha256(canonical.encode()).hexdigest()
    )


def test_native_cron_parser_and_next_occurrence(monkeypatch):
    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    monday = datetime.fromisoformat("2026-09-21T09:00:00+00:00")
    assert task_engine.cron_matches_at("0 9 * * 1-5", monday)
    assert not task_engine.cron_matches_at("0 9 * * 1-5", monday.replace(hour=10))
    assert task_engine.next_scheduled_at("@hourly", monday).isoformat() == (
        "2026-09-21T10:00:00+00:00"
    )


def test_scheduled_work_has_atomic_claims_leases_and_recurrence(monkeypatch):
    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tasks.sqlite3"
        monkeypatch.setattr(local_store, "_PATH", path)
        local_store._NATIVE_SCHEMA_READY.clear()
        task_engine.upsert_scheduled(
            {
                "id": "cron:one",
                "owner_id": "owner",
                "kind": "cron",
                "schedule_type": "cron",
                "schedule": "* * * * *",
                "due_at_ms": 1,
                "payload": {"job_id": "one"},
            }
        )

        def claim(worker):
            return task_engine.claim_scheduled(worker_id=worker, kind="cron", limit=1)

        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, [f"worker-{i}" for i in range(8)]))
        winners = [item[0] for item in claims if item]
        assert len(winners) == 1
        winner = winners[0]
        worker = next(f"worker-{i}" for i, item in enumerate(claims) if item)
        result = task_engine.complete_scheduled(
            winner["id"],
            worker_id=worker,
            lease_token=winner["lease_token"],
            success=True,
        )
        assert result["status"] == "pending"
        assert result["due_at_ms"] > int(datetime.now(timezone.utc).timestamp() * 1000)
        with pytest.raises(PermissionError, match="stale"):
            task_engine.complete_scheduled(
                winner["id"],
                worker_id="wrong",
                lease_token=winner["lease_token"],
                success=True,
            )


def test_atomic_create_or_replay_under_thread_contention(native_registry):
    spec = {
        "steps": [{"id": "read", "capability": "native-delayed-read"}],
        "completion_criteria": [],
        "permissions": [],
    }

    def create_once():
        return create_task(
            "owner",
            [{"id": "read", "capability": "native-delayed-read"}],
            idempotency_key="one-logical-request",
        )["id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(lambda _: create_once(), range(32)))

    assert len(set(identifiers)) == 1
    tasks = task_engine.list_owned("owner")
    assert len(tasks) == 1
    assert tasks[0]["idempotency_key"] == "one-logical-request"
    assert (
        tasks[0]["graph_hash"]
        == hashlib.sha256(
            json.dumps(spec, sort_keys=True, default=str).encode()
        ).hexdigest()
    )


@pytest.mark.asyncio
async def test_two_workers_cannot_execute_one_step_twice(native_registry):
    tool = native_registry
    task = create_task(
        "owner",
        [{"id": "read", "capability": "native-delayed-read"}],
        idempotency_key="single-execution",
    )
    first = DurableTaskRuntime()
    second = DurableTaskRuntime()

    await asyncio.gather(
        first.run("owner", task["id"]),
        second.run("owner", task["id"]),
    )

    final = first.inspect("owner", task["id"])
    assert final["status"] == "completed"
    assert final["steps"][0]["attempts"] == 1
    assert tool.calls == 1


def test_expired_lease_reclaim_fences_stale_worker(native_registry):
    task = create_task(
        "owner",
        [{"id": "read", "capability": "native-delayed-read", "max_attempts": 2}],
        idempotency_key="lease-fence",
    )
    path = str(local_store._PATH)
    first = json.loads(
        native.claim_step(
            path,
            "owner",
            task["id"],
            "read",
            "worker-a",
            "read_only",
            False,
            1_000,
            "1970-01-01T00:00:01+00:00",
            1_000,
        )
    )
    second = json.loads(
        native.claim_step(
            path,
            "owner",
            task["id"],
            "read",
            "worker-b",
            "read_only",
            False,
            2_001,
            "1970-01-01T00:00:02.001000+00:00",
            1_000,
        )
    )
    assert first["outcome"] == "claimed_execute"
    assert second["outcome"] == "claimed_execute"
    assert second["lease_token"] > first["lease_token"]

    with pytest.raises(PermissionError, match="stale"):
        native.complete_step(
            path,
            "owner",
            task["id"],
            "read",
            "worker-a",
            first["lease_token"],
            "stale result",
            "[]",
            2_500,
            datetime.now(timezone.utc).isoformat(),
            False,
        )
    completed = json.loads(
        native.complete_step(
            path,
            "owner",
            task["id"],
            "read",
            "worker-b",
            second["lease_token"],
            "fresh result",
            "[]",
            2_500,
            datetime.now(timezone.utc).isoformat(),
            False,
        )
    )
    assert completed["steps"][0]["result"] == "fresh result"


def test_owner_scope_and_fail_closed_required_mode(native_registry, monkeypatch):
    task = create_task(
        "owner-a",
        [{"id": "read", "capability": "native-delayed-read"}],
        idempotency_key="owner-boundary",
    )
    with pytest.raises(KeyError):
        task_engine.load("owner-b", task["id"])

    monkeypatch.setattr(task_engine, "_NATIVE_MODULE", None)
    monkeypatch.setattr(task_engine, "_NATIVE_IMPORT_ATTEMPTED", True)
    monkeypatch.setenv("CURIE_TASK_ENGINE", "rust")
    with pytest.raises(RuntimeError, match="native module is unavailable"):
        task_engine.use_native()
