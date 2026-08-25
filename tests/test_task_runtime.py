import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent.task_runtime import DurableTaskRuntime, create_task
from agent.tooling import ToolContext, ToolRegistry, ToolResult
from agent.tooling.registry import definition
from memory import local_store

pytestmark = [pytest.mark.integration, pytest.mark.security]


class RecordingTool:
    def __init__(self, name, *, read_only=True, fail_times=0, delay=0):
        self.name = name
        self.read_only = read_only
        self.fail_times = fail_times
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def execute(self, params, context):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.calls <= self.fail_times:
                raise RuntimeError("temporary failure")
            return ToolResult(
                text=f"{self.name} completed https://example.com/evidence",
                data={"owner": context.internal_id},
                source="test-source",
            )
        finally:
            self.active -= 1


@pytest.fixture()
def runtime_registry(monkeypatch):
    read = RecordingTool("read", delay=0.02)
    flaky = RecordingTool("flaky", fail_times=1)
    mutate = RecordingTool("mutate", read_only=False)
    verify = RecordingTool("verify")
    registry = ToolRegistry(
        [
            definition(read),
            definition(flaky),
            definition(mutate, required_permissions=("write_project",)),
            definition(verify),
        ]
    )
    monkeypatch.setattr("agent.task_runtime.get_runtime_registry", lambda: registry)
    return registry, read, flaky, mutate, verify


def test_graph_validation_cycles_mutation_verification_and_idempotency(
    runtime_registry,
):
    with pytest.raises(ValueError, match="cycle"):
        create_task(
            "u1",
            [
                {"id": "a", "capability": "read", "depends_on": ["b"]},
                {"id": "b", "capability": "read", "depends_on": ["a"]},
            ],
            idempotency_key="cycle",
        )
    with pytest.raises(ValueError, match="verification"):
        create_task(
            "u1",
            [{"id": "write", "capability": "mutate"}],
            idempotency_key="unsafe-write",
        )
    with pytest.raises(PermissionError, match="write_project"):
        create_task(
            "u1",
            [
                {
                    "id": "write",
                    "capability": "mutate",
                    "verification_steps": [{"capability": "verify"}],
                }
            ],
            idempotency_key="unprivileged-write",
        )
    task = create_task(
        "u1", [{"id": "read", "capability": "read"}], idempotency_key="same"
    )
    assert (
        create_task(
            "u1", [{"id": "read", "capability": "read"}], idempotency_key="same"
        )["id"]
        == task["id"]
    )
    with pytest.raises(ValueError, match="different task graph"):
        create_task(
            "u1", [{"id": "other", "capability": "read"}], idempotency_key="same"
        )


@pytest.mark.asyncio
async def test_independent_reads_run_concurrently_and_dependencies_wait(
    runtime_registry,
):
    _, read, _, _, _ = runtime_registry
    task = create_task(
        "u1",
        [
            {"id": "a", "capability": "read"},
            {"id": "b", "capability": "read"},
            {"id": "c", "capability": "read", "depends_on": ["a", "b"]},
        ],
        idempotency_key="parallel",
    )
    result = await DurableTaskRuntime().run("u1", task["id"])
    assert result["status"] == "completed"
    assert read.max_active == 2
    assert [step["status"] for step in result["steps"]] == [
        "completed",
        "completed",
        "completed",
    ]


@pytest.mark.asyncio
async def test_mutation_pauses_then_owner_approval_runs_once_and_verifies(
    runtime_registry,
):
    _, _, _, mutate, verify = runtime_registry
    task = create_task(
        "owner",
        [
            {
                "id": "write",
                "capability": "mutate",
                "verification_steps": [
                    {
                        "capability": "verify",
                        "completion_criteria": ["completed"],
                    }
                ],
            }
        ],
        idempotency_key="approved-write",
        completion_criteria=["mutate completed"],
        permissions={"write_project"},
    )
    runtime = DurableTaskRuntime()
    waiting = await runtime.run("owner", task["id"])
    assert waiting["status"] == "waiting_approval"
    assert mutate.calls == 0
    with pytest.raises(PermissionError):
        await runtime.approve_and_resume("other", task["id"], waiting["approval_token"])
    complete = await runtime.approve_and_resume(
        "owner", task["id"], waiting["approval_token"]
    )
    assert complete["status"] == "completed"
    assert mutate.calls == 1
    assert verify.calls == 1
    assert complete["steps"][0]["evidence"][0]["citations"]
    assert complete["audit"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_retry_resume_progress_cancellation_and_deadline(runtime_registry):
    _, _, flaky, _, _ = runtime_registry
    updates = []

    async def progress(update):
        updates.append(update)

    task = create_task(
        "u1",
        [{"id": "retry", "capability": "flaky", "max_attempts": 2}],
        idempotency_key="retry",
    )
    result = await DurableTaskRuntime().run("u1", task["id"], progress=progress)
    assert result["status"] == "completed"
    assert flaky.calls == 2
    assert updates[-1]["message"] == "Task completed and verified."

    cancelled = create_task(
        "u1", [{"id": "read", "capability": "read"}], idempotency_key="cancel"
    )
    runtime = DurableTaskRuntime()
    runtime.cancel("u1", cancelled["id"])
    assert (await runtime.run("u1", cancelled["id"]))["status"] == "cancelled"

    expired = create_task(
        "u1",
        [{"id": "read", "capability": "read"}],
        idempotency_key="deadline",
        deadline=datetime.now(timezone.utc) + timedelta(milliseconds=50),
    )
    await asyncio.sleep(0.06)
    assert (await runtime.run("u1", expired["id"]))["status"] == "expired"


def test_task_inspection_is_owner_scoped(runtime_registry):
    task = create_task(
        "owner-a", [{"id": "read", "capability": "read"}], idempotency_key="owned"
    )
    runtime = DurableTaskRuntime()
    assert runtime.inspect("owner-a", task["id"])["id"] == task["id"]
    with pytest.raises(KeyError):
        runtime.inspect("owner-b", task["id"])


@pytest.mark.asyncio
async def test_restart_retries_reads_but_never_replays_ambiguous_mutation(
    runtime_registry,
):
    _, _, _, mutate, verify = runtime_registry
    task = create_task(
        "owner",
        [
            {
                "id": "write",
                "capability": "mutate",
                "verification_steps": [
                    {
                        "capability": "verify",
                        "completion_criteria": ["completed"],
                    }
                ],
            }
        ],
        idempotency_key="interrupted-write",
        permissions={"write_project"},
    )
    task["status"] = "running"
    task["steps"][0]["status"] = "running"
    local_store.save_durable_task(task)

    result = await DurableTaskRuntime().run("owner", task["id"])

    assert result["status"] == "completed"
    assert mutate.calls == 0
    assert verify.calls == 1
    assert result["audit"][0]["status"] == "recovered_by_verification"
