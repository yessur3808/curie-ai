"""Crash-safe dependency-graph runtime for typed capability tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Awaitable, Callable
import uuid

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
    from memory.local_store import get_durable_task

    task = get_durable_task(owner_id, task_id)
    if not task:
        raise KeyError("Task does not exist or belongs to another user")
    return task


def _validate_graph(steps: list[dict]) -> None:
    if not steps:
        raise ValueError("A durable task requires at least one step")
    ids = [str(step.get("id", "")) for step in steps]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("Task step IDs must be non-empty and unique")
    known = set(ids)
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
        dependencies = set(step.get("depends_on", []))
        if not dependencies <= known or step["id"] in dependencies:
            raise ValueError(f"Task step {step['id']} has invalid dependencies")
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
    visiting, visited = set(), set()
    graph = {step["id"]: set(step.get("depends_on", [])) for step in steps}

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("Task dependency graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for step_id in ids:
        visit(step_id)


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
    from memory.local_store import get_durable_task_by_key

    granted_permissions = sorted(str(item) for item in permissions)
    task_spec = {
        "steps": steps,
        "completion_criteria": list(completion_criteria or []),
        "permissions": granted_permissions,
    }
    graph_hash = hashlib.sha256(
        json.dumps(task_spec, sort_keys=True, default=str).encode()
    ).hexdigest()
    existing = get_durable_task_by_key(str(owner_id), idempotency_key)
    if existing:
        if existing.get("graph_hash") != graph_hash:
            raise ValueError(
                "Idempotency key is already bound to a different task graph"
            )
        return existing
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
        step = {
            "id": str(source["id"]),
            "capability": str(source["capability"]),
            "params": dict(source.get("params", {})),
            "depends_on": list(source.get("depends_on", [])),
            "max_attempts": int(source.get("max_attempts", 1)),
            "completion_criteria": list(source.get("completion_criteria", [])),
            "verification_steps": list(source.get("verification_steps", [])),
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
        "cancel_requested": False,
        "waiting_step_id": None,
        "approval_token": None,
        "audit": [],
    }
    _store(document)
    return document


class DurableTaskRuntime:
    def __init__(self, *, global_read_limit: int = 8, per_user_read_limit: int = 3):
        self._global = asyncio.Semaphore(max(1, global_read_limit))
        self._per_user_limit = max(1, per_user_read_limit)
        self._users: dict[str, asyncio.Semaphore] = {}

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

    async def run(
        self,
        owner_id: str,
        task_id: str,
        *,
        profile: dict | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict:
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
        task = _load(owner_id, task_id)
        if task["status"] not in _TERMINAL:
            task["cancel_requested"] = True
            task["status"] = "cancelled"
            _store(task)
        return task

    def inspect(self, owner_id: str, task_id: str) -> dict:
        return _load(owner_id, task_id)


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
        from memory.local_store import list_durable_tasks

        tasks = list_durable_tasks(str(owner_id))[-10:]
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
