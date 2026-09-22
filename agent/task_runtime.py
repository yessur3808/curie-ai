"""Crash-safe dependency-graph runtime for typed capability tasks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
import os
import re
from typing import Any, Awaitable, Callable
import uuid

from agent import task_engine
from agent.tooling import ToolContext, ToolResult, get_runtime_registry

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
_TERMINAL = {"completed", "failed", "cancelled", "expired"}
_URL = re.compile(r"https?://[^\s)]+", re.I)
_COMMAND = re.compile(
    r"^/?task\s+(inspect|cancel|resume|approve)\s+([a-f0-9]{32})"
    r"(?:\s+([a-f0-9]{8}))?$",
    re.I,
)
_LIST_COMMAND = re.compile(r"^/?task(?:\s+list)?$", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _store(document: dict) -> None:
    from memory.local_store import save_durable_task

    document["updated_at"] = _now()
    save_durable_task(document)


def _load(owner_id: str, task_id: str) -> dict:
    if task_engine.use_native():
        return task_engine.load(owner_id, task_id)
    from memory.local_store import get_durable_task

    task = get_durable_task(owner_id, task_id)
    if not task:
        raise KeyError("Task does not exist or belongs to another user")
    return task


def _validate_graph(steps: list[dict]) -> None:
    task_engine.validate_task_graph(steps)
    registry = get_runtime_registry()
    for step in steps:
        if set(step) - {
            "id",
            "capability",
            "params",
            "depends_on",
            "max_attempts",
            "completion_criteria",
            "verification_steps",
        }:
            raise ValueError(f"Task step {step['id']} has unsupported fields")
        capability = registry.get(str(step.get("capability", "")))
        attempts = int(step.get("max_attempts", 1))
        if not 1 <= attempts <= 5:
            raise ValueError("Step max_attempts must be between 1 and 5")
        if capability.risk == "mutating" and attempts != 1:
            raise ValueError("Mutating steps cannot be retried automatically")
        if capability.risk == "mutating" and not step.get("verification_steps"):
            raise ValueError("Mutating task steps require explicit verification_steps")
        for verification in step.get("verification_steps", []):
            verifier = registry.get(str(verification.get("capability", "")))
            if verifier.risk != "read_only":
                raise ValueError("Verification capabilities must be read-only")


def create_task(
    owner_id: str,
    steps: list[dict],
    *,
    idempotency_key: str,
    deadline: datetime | None = None,
    completion_criteria: list[str] | None = None,
    permissions: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> dict:
    """Persist an immutable task graph or return the existing idempotent task."""
    if not owner_id or not idempotency_key.strip():
        raise ValueError("Owner and idempotency_key are required")
    granted_permissions = sorted(str(item) for item in permissions)
    task_spec = {
        "steps": steps,
        "completion_criteria": list(completion_criteria or []),
        "permissions": granted_permissions,
    }
    # Preserve the original serialized form so existing task keys remain
    # compatible across the engine migration.
    graph_hash = task_engine.hash_text(
        json.dumps(task_spec, sort_keys=True, default=str)
    )
    _validate_graph(steps)
    for step in steps:
        definition = get_runtime_registry().get(str(step["capability"]))
        if not definition.required_permissions <= set(granted_permissions):
            missing = sorted(definition.required_permissions - set(granted_permissions))
            raise PermissionError(
                f"Task is missing permissions for {step['capability']!r}: "
                f"{', '.join(missing)}"
            )
    now = _now()
    deadline = deadline or now + timedelta(hours=1)
    if deadline <= now:
        raise ValueError("Task deadline must be in the future")
    normalized = []
    for source in steps:
        definition = get_runtime_registry().get(str(source["capability"]))
        step = {
            "id": str(source["id"]),
            "capability": str(source["capability"]),
            "params": dict(source.get("params", {})),
            "depends_on": list(source.get("depends_on", [])),
            "max_attempts": int(source.get("max_attempts", 1)),
            "completion_criteria": list(source.get("completion_criteria", [])),
            "verification_steps": list(source.get("verification_steps", [])),
            "risk": definition.risk,
            "status": "pending",
            "attempts": 0,
            "result": None,
            "evidence": [],
            "error": None,
        }
        normalized.append(step)
    task_id = uuid.uuid4().hex
    document = {
        "id": task_id,
        "owner_id": str(owner_id),
        "idempotency_key": idempotency_key[:128],
        "graph_hash": graph_hash,
        "status": "pending",
        "steps": normalized,
        "completion_criteria": list(completion_criteria or []),
        "permissions": granted_permissions,
        "created_at": now,
        "updated_at": now,
        "deadline": deadline,
        "deadline_epoch_ms": int(deadline.timestamp() * 1000),
        "cancel_requested": False,
        "waiting_step_id": None,
        "approval_token": None,
        "audit": [],
        "revision": 1,
    }
    return task_engine.create_or_get(document)[0]


class DurableTaskRuntime:
    def __init__(self, *, global_read_limit: int = 8, per_user_read_limit: int = 3):
        self._global = asyncio.Semaphore(max(1, global_read_limit))
        self._per_user_limit = max(1, per_user_read_limit)
        self._users: dict[str, asyncio.Semaphore] = {}
        self._worker_id = uuid.uuid4().hex
        self._lease_ms = max(
            1_000, min(300_000, int(os.getenv("CURIE_TASK_LEASE_MS", "30000")))
        )

    @staticmethod
    def interrupted_step_action(risk: str) -> str:
        """Return the only safe restart behavior for an interrupted task step."""
        return "verify_without_replay" if risk == "mutating" else "retry_read"

    async def _progress(
        self, callback: ProgressCallback | None, task: dict, message: str
    ) -> None:
        if callback is None:
            return
        update = {
            "task_id": task["id"],
            "status": task["status"],
            "message": message[:240],
        }
        result = callback(update)
        if asyncio.iscoroutine(result):
            await result

    async def _execute_capability(
        self,
        owner_id: str,
        capability: str,
        params: dict,
        profile: dict,
        permissions: frozenset[str],
        *,
        approved: bool,
    ) -> ToolResult:
        definition = get_runtime_registry().get(capability)
        context = ToolContext(
            internal_id=owner_id,
            profile=profile,
            permissions=permissions,
            approved=approved,
        )
        started = _now()
        try:
            result = await get_runtime_registry().execute(capability, params, context)
            from memory.repositories import get_repositories

            get_repositories().audits.append(
                owner_id,
                capability,
                "completed",
                {
                    "connector": "task_runtime",
                    "validated_action": capability,
                    "parameters": params,
                    "policy_decision": "allowed",
                    "approval": {
                        "required": definition.risk == "mutating",
                        "granted": approved,
                    },
                    "tool_version": definition.version,
                    "started_at": started,
                    "finished_at": _now(),
                    "changed_files": list(result.data.get("changed_files", [])),
                    "command_exit_status": (
                        result.data.get("exit_status", 0)
                        if "command" in result.data
                        else None
                    ),
                    "citations": _URL.findall(result.text)[:20],
                    "outcome": {"status": "completed", "summary": result.text[:1000]},
                },
            )
            return result
        except Exception as exc:
            from memory.repositories import get_repositories

            get_repositories().audits.append(
                owner_id,
                capability,
                "failed",
                {
                    "connector": "task_runtime",
                    "validated_action": capability,
                    "parameters": params,
                    "policy_decision": "execution_failed",
                    "approval": {
                        "required": definition.risk == "mutating",
                        "granted": approved,
                    },
                    "tool_version": definition.version,
                    "started_at": started,
                    "finished_at": _now(),
                    "outcome": {"status": "failed", "error": str(exc)},
                    "security_category": "tool_failure",
                },
            )
            raise

    async def _run_step(self, task: dict, step: dict, profile: dict) -> None:
        definition = get_runtime_registry().get(step["capability"])
        step["status"] = "running"
        _store(task)
        last_error = None
        for _ in range(step["attempts"], step["max_attempts"]):
            step["attempts"] += 1
            try:

                async def execute():
                    return await self._execute_capability(
                        task["owner_id"],
                        step["capability"],
                        step["params"],
                        profile,
                        frozenset(task.get("permissions", ())),
                        approved=definition.risk == "mutating",
                    )

                if definition.risk == "read_only":
                    user_slot = self._users.setdefault(
                        task["owner_id"], asyncio.Semaphore(self._per_user_limit)
                    )
                    async with self._global, user_slot:
                        result = await execute()
                else:
                    result = await execute()
                self._verify_result(step, result)
                verification = []
                for check in step["verification_steps"]:
                    checked = await self._execute_capability(
                        task["owner_id"],
                        check["capability"],
                        dict(check.get("params", {})),
                        profile,
                        frozenset(task.get("permissions", ())),
                        approved=False,
                    )
                    self._verify_text(
                        check.get("completion_criteria", []), checked.text
                    )
                    verification.append(self._evidence(checked))
                step["result"] = result.text
                step["evidence"] = [self._evidence(result), *verification]
                step["status"] = "completed"
                step["error"] = None
                task["audit"].append(
                    {
                        "step_id": step["id"],
                        "status": "completed",
                        "attempt": step["attempts"],
                        "evidence": step["evidence"],
                        "at": _now(),
                    }
                )
                _store(task)
                return
            except Exception as exc:
                last_error = str(exc)[:500]
                step["error"] = last_error
                _store(task)
        step["status"] = "failed"
        task["audit"].append(
            {
                "step_id": step["id"],
                "status": "failed",
                "attempts": step["attempts"],
                "error": last_error,
                "at": _now(),
            }
        )
        _store(task)

    async def _recover_interrupted_mutation(
        self, task: dict, step: dict, profile: dict
    ) -> None:
        """Reconcile an ambiguous write after restart without replaying it."""
        evidence = []
        try:
            for check in step["verification_steps"]:
                result = await self._execute_capability(
                    task["owner_id"],
                    check["capability"],
                    dict(check.get("params", {})),
                    profile,
                    frozenset(task.get("permissions", ())),
                    approved=False,
                )
                self._verify_text(check.get("completion_criteria", []), result.text)
                evidence.append(self._evidence(result))
            step["status"] = "completed"
            step["result"] = "Interrupted mutation reconciled by verification."
            step["evidence"] = evidence
            step["error"] = None
            task["audit"].append(
                {
                    "step_id": step["id"],
                    "status": "recovered_by_verification",
                    "evidence": evidence,
                    "at": _now(),
                }
            )
        except Exception as exc:
            step["status"] = "failed"
            step["error"] = (
                "Interrupted mutation was not replayed; verification failed: "
                f"{str(exc)[:400]}"
            )
        _store(task)

    @staticmethod
    def _verify_text(criteria: list[str], text: str) -> None:
        if not text.strip():
            raise ValueError("Capability returned no verifiable output")
        missing = [item for item in criteria if item.casefold() not in text.casefold()]
        if missing:
            raise ValueError(
                f"Completion criteria missing from output: {', '.join(missing)}"
            )

    def _verify_result(self, step: dict, result: ToolResult) -> None:
        if not isinstance(result, ToolResult):
            raise TypeError("Task capability returned an invalid result")
        self._verify_text(step["completion_criteria"], result.text)

    @staticmethod
    def _evidence(result: ToolResult) -> dict:
        return {
            "source": result.source,
            "citations": _URL.findall(result.text)[:20],
            "output": result.text[:4000],
            "data": dict(result.data),
        }

    async def _lease_heartbeat(
        self,
        owner_id: str,
        task_id: str,
        step_id: str,
        lease_token: int,
    ) -> None:
        interval = max(0.5, self._lease_ms / 3000)
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(
                task_engine.heartbeat,
                owner_id,
                task_id,
                step_id,
                worker_id=self._worker_id,
                lease_token=lease_token,
                lease_ms=self._lease_ms,
            )

    async def _run_native_step(
        self,
        owner_id: str,
        task_id: str,
        step_id: str,
        profile: dict,
        *,
        approved: bool = False,
    ) -> dict:
        task = task_engine.load(owner_id, task_id)
        source = next(item for item in task["steps"] if item["id"] == step_id)
        definition = get_runtime_registry().get(source["capability"])
        claim = task_engine.claim(
            owner_id,
            task_id,
            step_id,
            worker_id=self._worker_id,
            risk=definition.risk,
            approved=approved,
            lease_ms=self._lease_ms,
        )
        if claim["outcome"] not in {"claimed_execute", "claimed_reconcile"}:
            return claim["task"]
        lease_token = int(claim["lease_token"])
        step = claim["step"]
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(owner_id, task_id, step_id, lease_token)
        )
        try:
            if claim["outcome"] == "claimed_reconcile":
                evidence = []
                for check in step["verification_steps"]:
                    checked = await self._execute_capability(
                        owner_id,
                        check["capability"],
                        dict(check.get("params", {})),
                        profile,
                        frozenset(task.get("permissions", ())),
                        approved=False,
                    )
                    self._verify_text(
                        check.get("completion_criteria", []), checked.text
                    )
                    evidence.append(self._evidence(checked))
                return task_engine.complete(
                    owner_id,
                    task_id,
                    step_id,
                    worker_id=self._worker_id,
                    lease_token=lease_token,
                    result_text="Interrupted mutation reconciled by verification.",
                    evidence=evidence,
                    recovered=True,
                )

            async def execute():
                return await self._execute_capability(
                    owner_id,
                    step["capability"],
                    step["params"],
                    profile,
                    frozenset(task.get("permissions", ())),
                    approved=definition.risk == "mutating",
                )

            if definition.risk == "read_only":
                user_slot = self._users.setdefault(
                    owner_id, asyncio.Semaphore(self._per_user_limit)
                )
                async with self._global, user_slot:
                    result = await execute()
            else:
                result = await execute()
            self._verify_result(step, result)
            verification = []
            for check in step["verification_steps"]:
                checked = await self._execute_capability(
                    owner_id,
                    check["capability"],
                    dict(check.get("params", {})),
                    profile,
                    frozenset(task.get("permissions", ())),
                    approved=False,
                )
                self._verify_text(check.get("completion_criteria", []), checked.text)
                verification.append(self._evidence(checked))
            return task_engine.complete(
                owner_id,
                task_id,
                step_id,
                worker_id=self._worker_id,
                lease_token=lease_token,
                result_text=result.text,
                evidence=[self._evidence(result), *verification],
            )
        except Exception as exc:
            attempt = int(step.get("attempts", 1))
            base_delay = max(
                0, min(30_000, int(os.getenv("CURIE_TASK_RETRY_BASE_MS", "100")))
            )
            delay = min(300_000, base_delay * (2 ** max(0, attempt - 1)))
            try:
                return task_engine.fail(
                    owner_id,
                    task_id,
                    step_id,
                    worker_id=self._worker_id,
                    lease_token=lease_token,
                    error=str(exc),
                    retry_allowed=definition.risk == "read_only",
                    retry_delay_ms=delay,
                )
            except PermissionError:
                return task_engine.load(owner_id, task_id)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat

    async def _run_native(
        self,
        owner_id: str,
        task_id: str,
        *,
        profile: dict,
        progress: ProgressCallback | None,
    ) -> dict:
        initial = task_engine.load(owner_id, task_id)
        if initial["status"] in _TERMINAL or initial["status"] == "waiting_approval":
            return initial
        await self._progress(progress, initial, "Task resumed.")
        while True:
            state = task_engine.reconcile(owner_id, task_id)
            task = state["task"]
            if state["outcome"] in {"terminal", "waiting_approval"}:
                return task
            if state["outcome"] == "ready_to_finalize":
                combined = "\n".join(
                    str(step.get("result") or "") for step in task["steps"]
                )
                try:
                    self._verify_text(task["completion_criteria"], combined)
                except ValueError as exc:
                    return task_engine.finalize(
                        owner_id, task_id, success=False, error=str(exc)
                    )
                task = task_engine.finalize(owner_id, task_id, success=True, error=None)
                await self._progress(progress, task, "Task completed and verified.")
                return task
            ready_ids = list(state.get("ready_step_ids", []))
            if not ready_ids:
                running_ids = [
                    step["id"]
                    for step in task["steps"]
                    if step.get("status") == "running"
                ]
                if running_ids:
                    await asyncio.gather(
                        *(
                            self._run_native_step(
                                owner_id, task_id, step_id, profile, approved=False
                            )
                            for step_id in running_ids
                        )
                    )
                    refreshed = task_engine.load(owner_id, task_id)
                    if all(
                        step.get("status") == "running"
                        for step in refreshed["steps"]
                        if step["id"] in running_ids
                    ):
                        return refreshed
                    continue
                deferred_ms = state.get("deferred_ms")
                if deferred_ms is not None:
                    await asyncio.sleep(min(max(int(deferred_ms), 1) / 1000, 1.0))
                    continue
                return task
            pending = [
                next(step for step in task["steps"] if step["id"] == step_id)
                for step_id in ready_ids
            ]
            mutating = next(
                (
                    step
                    for step in pending
                    if get_runtime_registry().get(step["capability"]).risk == "mutating"
                ),
                None,
            )
            if mutating:
                from memory.repositories import get_repositories

                token = get_repositories().approvals.create(
                    owner_id,
                    {
                        "action": "durable_task_step",
                        "task_id": task_id,
                        "step_id": mutating["id"],
                    },
                )
                task = task_engine.wait_for_approval(
                    owner_id, task_id, mutating["id"], token
                )
                await self._progress(
                    progress, task, f"Approval required for step {mutating['id']}."
                )
                return task
            await asyncio.gather(
                *(
                    self._run_native_step(
                        owner_id, task_id, step_id, profile, approved=False
                    )
                    for step_id in ready_ids
                )
            )

    async def run(
        self,
        owner_id: str,
        task_id: str,
        *,
        profile: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict:
        if task_engine.use_native():
            return await self._run_native(
                str(owner_id),
                task_id,
                profile=profile or {},
                progress=progress,
            )
        task = _load(str(owner_id), task_id)
        if task["status"] in _TERMINAL:
            return task
        if task["status"] == "waiting_approval":
            return task
        if _parse_time(task["deadline"]) <= _now():
            task["status"] = "expired"
            _store(task)
            return task
        if task.get("cancel_requested"):
            task["status"] = "cancelled"
            _store(task)
            return task
        # A process may have stopped after persisting running state. Reads are
        # safe to retry; writes are reconciled through their required verifier
        # and are never replayed automatically.
        for step in task["steps"]:
            if step["status"] == "running":
                definition = get_runtime_registry().get(step["capability"])
                action = self.interrupted_step_action(definition.risk)
                if action == "verify_without_replay":
                    await self._recover_interrupted_mutation(task, step, profile or {})
                else:
                    step["status"] = "pending"
        task["status"] = "running"
        _store(task)
        await self._progress(progress, task, "Task resumed.")
        while True:
            if _parse_time(task["deadline"]) <= _now():
                task["status"] = "expired"
                _store(task)
                return task
            if task.get("cancel_requested") or _load(owner_id, task_id).get(
                "cancel_requested"
            ):
                task["status"] = "cancelled"
                _store(task)
                await self._progress(progress, task, "Task cancelled.")
                return task
            completed = {
                step["id"] for step in task["steps"] if step["status"] == "completed"
            }
            failed = [step for step in task["steps"] if step["status"] == "failed"]
            if failed:
                task["status"] = "failed"
                _store(task)
                return task
            pending = [
                step
                for step in task["steps"]
                if step["status"] == "pending" and set(step["depends_on"]) <= completed
            ]
            if not pending:
                if len(completed) == len(task["steps"]):
                    combined = "\n".join(
                        str(step.get("result") or "") for step in task["steps"]
                    )
                    try:
                        self._verify_text(task["completion_criteria"], combined)
                    except ValueError as exc:
                        task["status"] = "failed"
                        task["error"] = str(exc)
                        _store(task)
                        return task
                    task["status"] = "completed"
                    task["completed_at"] = _now()
                    _store(task)
                    await self._progress(progress, task, "Task completed and verified.")
                return task
            mutating = next(
                (
                    step
                    for step in pending
                    if get_runtime_registry().get(step["capability"]).risk == "mutating"
                ),
                None,
            )
            if mutating:
                from memory.repositories import get_repositories

                token = get_repositories().approvals.create(
                    owner_id,
                    {
                        "action": "durable_task_step",
                        "task_id": task_id,
                        "step_id": mutating["id"],
                    },
                )
                task["status"] = "waiting_approval"
                task["waiting_step_id"] = mutating["id"]
                task["approval_token"] = token
                _store(task)
                await self._progress(
                    progress, task, f"Approval required for step {mutating['id']}."
                )
                return task
            await asyncio.gather(
                *(self._run_step(task, step, profile or {}) for step in pending)
            )

    async def approve_and_resume(
        self,
        owner_id: str,
        task_id: str,
        token: str,
        *,
        profile: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict:
        if task_engine.use_native():
            try:
                task = task_engine.load(owner_id, task_id)
            except KeyError as exc:
                raise PermissionError(
                    "Task approval is invalid or belongs to another user"
                ) from exc
            if task["status"] != "waiting_approval" or token != task.get(
                "approval_token"
            ):
                raise PermissionError("Task is not waiting for this approval token")
            from memory.repositories import get_repositories

            approval = get_repositories().approvals.consume(owner_id, token, True)
            if (
                not approval
                or approval.get("task_id") != task_id
                or approval.get("step_id") != task["waiting_step_id"]
            ):
                raise PermissionError(
                    "Approval is invalid, expired, consumed, or belongs to another user"
                )
            await self._run_native_step(
                owner_id,
                task_id,
                task["waiting_step_id"],
                profile or {},
                approved=True,
            )
            return await self._run_native(
                owner_id,
                task_id,
                profile=profile or {},
                progress=progress,
            )
        try:
            task = _load(owner_id, task_id)
        except KeyError as exc:
            raise PermissionError(
                "Task approval is invalid or belongs to another user"
            ) from exc
        if task["status"] != "waiting_approval" or token != task.get("approval_token"):
            raise PermissionError("Task is not waiting for this approval token")
        from memory.repositories import get_repositories

        approval = get_repositories().approvals.consume(owner_id, token, True)
        if (
            not approval
            or approval.get("task_id") != task_id
            or approval.get("step_id") != task["waiting_step_id"]
        ):
            raise PermissionError(
                "Approval is invalid, expired, consumed, or belongs to another user"
            )
        step = next(
            item for item in task["steps"] if item["id"] == task["waiting_step_id"]
        )
        task["status"] = "running"
        task["waiting_step_id"] = None
        task["approval_token"] = None
        await self._run_step(task, step, profile or {})
        return await self.run(owner_id, task_id, profile=profile, progress=progress)

    def cancel(self, owner_id: str, task_id: str) -> dict:
        if task_engine.use_native():
            return task_engine.cancel(owner_id, task_id)
        task = _load(owner_id, task_id)
        if task["status"] not in _TERMINAL:
            task["cancel_requested"] = True
            task["status"] = "cancelled"
            _store(task)
        return task

    def inspect(self, owner_id: str, task_id: str) -> dict:
        return task_engine.load(owner_id, task_id)


_runtime: DurableTaskRuntime | None = None


def get_task_runtime() -> DurableTaskRuntime:
    global _runtime
    if _runtime is None:
        _runtime = DurableTaskRuntime()
    return _runtime


def reset_task_runtime() -> None:
    global _runtime
    _runtime = None


async def handle_task_command(
    owner_id: str, text: str, *, profile: dict | None = None
) -> str | None:
    """Inspect, cancel, resume, or approve an owner-scoped durable task."""
    if _LIST_COMMAND.fullmatch(text.strip()):
        tasks = task_engine.list_owned(str(owner_id), limit=10)
        if not tasks:
            return "You have no retained tasks."
        return "Recent tasks:\n" + "\n".join(
            f"- `{task['id']}` — {task['status']}" for task in reversed(tasks)
        )
    match = _COMMAND.fullmatch(text.strip())
    if not match:
        return None
    action, task_id, token = match.group(1).casefold(), match.group(2), match.group(3)
    runtime = get_task_runtime()
    if action == "inspect":
        return json.dumps(runtime.inspect(owner_id, task_id), default=str, indent=2)
    if action == "cancel":
        task = runtime.cancel(owner_id, task_id)
        return f"Task `{task_id}` is {task['status']}."
    if action == "resume":
        task = await runtime.run(owner_id, task_id, profile=profile)
        return _task_summary(task)
    if not token:
        return "Use `/task approve <task-id> <approval-token>`."
    task = await runtime.approve_and_resume(owner_id, task_id, token, profile=profile)
    return _task_summary(task)


def _task_summary(task: dict) -> str:
    completed = sum(step["status"] == "completed" for step in task["steps"])
    summary = f"Task `{task['id']}` is {task['status']} ({completed}/{len(task['steps'])} steps complete)."
    if task["status"] == "waiting_approval":
        summary += (
            f" Approve step `{task['waiting_step_id']}` with `/task approve "
            f"{task['id']} {task['approval_token']}`."
        )
    elif task["status"] == "completed":
        resources = []
        for step in task["steps"]:
            for evidence in step.get("evidence", []):
                data = evidence.get("data") or {}
                resources.extend(str(item) for item in data.get("changed_files", []))
                for key in ("resource_id", "item_id", "event_id", "reminder_id"):
                    if data.get(key):
                        resources.append(str(data[key]))
        affected = ", ".join(dict.fromkeys(resources) or ["no named resources"])
        summary += (
            f" Receipt: verified {completed} step(s); affected: {affected}. "
            f"Recovery: inspect with `/task inspect {task['id']}`; "
            "use the affected service’s undo/delete operation where available."
        )
    elif task["status"] in {"failed", "cancelled", "expired"}:
        summary += (
            f" No unfinished mutation will be replayed automatically. Inspect with "
            f"`/task inspect {task['id']}` before retrying."
        )
    return summary
