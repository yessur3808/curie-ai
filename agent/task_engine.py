"""Backend selection and typed bridge for Curie's durable task engine."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import threading
from typing import Any

_NATIVE_MODULE = None
_NATIVE_IMPORT_ATTEMPTED = False
_NATIVE_IMPORT_ERROR: str | None = None
_METRICS_LOCK = threading.Lock()
_METRICS = {
    "native_operations": 0,
    "python_operations": 0,
    "idempotent_replays": 0,
    "claims": 0,
    "reconciliations": 0,
    "stale_lease_rejections": 0,
}


def _mode() -> str:
    configured = os.getenv("CURIE_TASK_ENGINE", "auto").strip().casefold()
    return configured if configured in {"auto", "rust", "python"} else "auto"


def _native_module():
    global _NATIVE_MODULE, _NATIVE_IMPORT_ATTEMPTED, _NATIVE_IMPORT_ERROR
    if _NATIVE_IMPORT_ATTEMPTED:
        return _NATIVE_MODULE
    _NATIVE_IMPORT_ATTEMPTED = True
    try:
        import _curie_task_engine as native

        _NATIVE_MODULE = native
        _NATIVE_IMPORT_ERROR = None
    except (ImportError, OSError) as exc:
        _NATIVE_MODULE = None
        _NATIVE_IMPORT_ERROR = type(exc).__name__
    return _NATIVE_MODULE


def task_engine_status() -> dict:
    mode = _mode()
    native = _native_module()
    available = native is not None
    version = None
    if available:
        try:
            version = str(native.engine_version())
        except Exception:
            available = False
    active = "python" if mode == "python" or not available else "rust"
    return {
        "mode": mode,
        "available": available,
        "active": active,
        "version": version,
        "fallback": active == "python" and mode != "python",
        "import_error": _NATIVE_IMPORT_ERROR,
    }


def task_engine_metrics(*, reset: bool = False) -> dict:
    with _METRICS_LOCK:
        result = dict(_METRICS)
        if reset:
            for key in _METRICS:
                _METRICS[key] = 0
    result["engine"] = task_engine_status()
    return result


def _record(key: str) -> None:
    with _METRICS_LOCK:
        _METRICS[key] += 1


def use_native() -> bool:
    status = task_engine_status()
    if status["mode"] == "rust" and status["active"] != "rust":
        raise RuntimeError(
            "CURIE_TASK_ENGINE=rust but the native module is unavailable"
        )
    return status["active"] == "rust"


def _native():
    if not use_native():
        raise RuntimeError("The Rust task engine is not active")
    return _native_module()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def hash_payload(value: Any) -> str:
    raw = _canonical_json(value)
    return hash_text(raw)


def hash_text(raw: str) -> str:
    if use_native():
        _record("native_operations")
        return str(_native().hash_canonical_json(raw))
    _record("python_operations")
    return hashlib.sha256(raw.encode()).hexdigest()


def stable_key(payload: dict[str, Any]) -> str:
    return hash_payload(payload)


def validate_task_graph(steps: list[dict]) -> list[str]:
    graph = [
        {
            "id": str(step.get("id", "")),
            "depends_on": list(step.get("depends_on", [])),
        }
        for step in steps
    ]
    if use_native():
        _record("native_operations")
        return list(_native().validate_graph(_canonical_json(graph)))
    _record("python_operations")
    if not graph:
        raise ValueError("A durable task requires at least one step")
    identifiers = [item["id"] for item in graph]
    if any(not item for item in identifiers) or len(identifiers) != len(
        set(identifiers)
    ):
        raise ValueError("Task step IDs must be non-empty and unique")
    known = set(identifiers)
    dependencies = {item["id"]: set(item["depends_on"]) for item in graph}
    for step_id, values in dependencies.items():
        if not values <= known or step_id in values:
            raise ValueError(f"Task step {step_id} has invalid dependencies")
    visiting: set[str] = set()
    visited: set[str] = set()
    result: list[str] = []

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise ValueError("Task dependency graph contains a cycle")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in dependencies[step_id]:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)
        result.append(step_id)

    for identifier in identifiers:
        visit(identifier)
    return result


def retry_decision(
    *,
    attempt: int,
    max_attempts: int,
    read_only: bool,
    idempotency_mode: str,
    error_kind: str,
    explicit_retryable: bool = False,
    retry_after_ms: int = 0,
    cancellation_requested: bool = False,
) -> tuple[bool, str, int]:
    if use_native():
        _record("native_operations")
        retry, reason, delay_ms = _native().decide_retry(
            int(attempt),
            int(max_attempts),
            bool(read_only),
            str(idempotency_mode),
            str(error_kind),
            bool(explicit_retryable),
            int(retry_after_ms),
            bool(cancellation_requested),
        )
        return bool(retry), str(reason), int(delay_ms)
    _record("python_operations")
    if cancellation_requested:
        return False, "user_cancelled", 0
    if attempt >= max_attempts:
        return False, "attempt_limit", 0
    if error_kind in {"value", "permission", "lookup"}:
        return False, "non_retryable_input_or_policy", 0
    if not explicit_retryable and error_kind not in {"timeout", "connection"}:
        return False, "error_not_transient", 0
    if not read_only and idempotency_mode not in {"state_reconciled", "provider_key"}:
        return False, "mutation_not_safely_idempotent", 0
    return True, "transient_safe_retry", min(max(int(retry_after_ms), 0), 5_000)


def _prepare_store() -> str:
    from memory import local_store

    local_store.initialize_durable_task_store()
    return str(local_store._PATH)


def _now_values() -> tuple[int, str]:
    now = datetime.now(timezone.utc)
    return int(now.timestamp() * 1000), now.isoformat()


def create_or_get(document: dict) -> tuple[dict, bool]:
    if not use_native():
        from memory.local_store import get_durable_task_by_key, save_durable_task

        existing = get_durable_task_by_key(
            str(document["owner_id"]), str(document["idempotency_key"])
        )
        if existing:
            if existing.get("graph_hash") != document.get("graph_hash"):
                raise ValueError(
                    "Idempotency key is already bound to a different task graph"
                )
            _record("idempotent_replays")
            return existing, True
        save_durable_task(document)
        _record("python_operations")
        return document, False
    path = _prepare_store()
    now_ms, now_iso = _now_values()
    raw, replay = _native().create_or_get(path, _canonical_json(document), now_iso)
    _record("native_operations")
    if replay:
        _record("idempotent_replays")
    return json.loads(raw), bool(replay)


def load(owner_id: str, task_id: str) -> dict:
    if not use_native():
        from memory.local_store import get_durable_task

        task = get_durable_task(str(owner_id), str(task_id))
        if not task:
            raise KeyError("Task does not exist or belongs to another user")
        _record("python_operations")
        return task
    raw = _native().load_task(_prepare_store(), str(owner_id), str(task_id))
    _record("native_operations")
    return json.loads(raw)


def list_owned(
    owner_id: str, status: str | None = None, limit: int = 100
) -> list[dict]:
    if not use_native():
        from memory.local_store import list_durable_tasks

        _record("python_operations")
        return list_durable_tasks(str(owner_id), status)[: max(1, int(limit))]
    rows = _native().list_tasks(
        _prepare_store(), str(owner_id), status, max(1, int(limit))
    )
    _record("native_operations")
    return [json.loads(item) for item in rows]


def reconcile(owner_id: str, task_id: str) -> dict:
    now_ms, now_iso = _now_values()
    raw = _native().reconcile_task(
        _prepare_store(), str(owner_id), str(task_id), now_ms, now_iso
    )
    _record("native_operations")
    _record("reconciliations")
    return json.loads(raw)


def claim(
    owner_id: str,
    task_id: str,
    step_id: str,
    *,
    worker_id: str,
    risk: str,
    approved: bool,
    lease_ms: int,
) -> dict:
    now_ms, now_iso = _now_values()
    raw = _native().claim_step(
        _prepare_store(),
        str(owner_id),
        str(task_id),
        str(step_id),
        str(worker_id),
        str(risk),
        bool(approved),
        now_ms,
        now_iso,
        int(lease_ms),
    )
    result = json.loads(raw)
    _record("native_operations")
    if str(result.get("outcome", "")).startswith("claimed_"):
        _record("claims")
    return result


def heartbeat(
    owner_id: str,
    task_id: str,
    step_id: str,
    *,
    worker_id: str,
    lease_token: int,
    lease_ms: int,
) -> int:
    now_ms, now_iso = _now_values()
    result = _native().heartbeat_step(
        _prepare_store(),
        str(owner_id),
        str(task_id),
        str(step_id),
        str(worker_id),
        int(lease_token),
        now_ms,
        now_iso,
        int(lease_ms),
    )
    _record("native_operations")
    return int(result)


def complete(
    owner_id: str,
    task_id: str,
    step_id: str,
    *,
    worker_id: str,
    lease_token: int,
    result_text: str,
    evidence: list[dict],
    recovered: bool = False,
) -> dict:
    now_ms, now_iso = _now_values()
    try:
        raw = _native().complete_step(
            _prepare_store(),
            str(owner_id),
            str(task_id),
            str(step_id),
            str(worker_id),
            int(lease_token),
            str(result_text),
            _canonical_json(evidence),
            now_ms,
            now_iso,
            bool(recovered),
        )
    except PermissionError:
        _record("stale_lease_rejections")
        raise
    _record("native_operations")
    return json.loads(raw)


def fail(
    owner_id: str,
    task_id: str,
    step_id: str,
    *,
    worker_id: str,
    lease_token: int,
    error: str,
    retry_allowed: bool,
    retry_delay_ms: int,
) -> dict:
    now_ms, now_iso = _now_values()
    try:
        raw = _native().fail_step(
            _prepare_store(),
            str(owner_id),
            str(task_id),
            str(step_id),
            str(worker_id),
            int(lease_token),
            str(error)[:500],
            bool(retry_allowed),
            int(retry_delay_ms),
            now_ms,
            now_iso,
        )
    except PermissionError:
        _record("stale_lease_rejections")
        raise
    _record("native_operations")
    return json.loads(raw)


def wait_for_approval(
    owner_id: str, task_id: str, step_id: str, approval_token: str
) -> dict:
    _, now_iso = _now_values()
    raw = _native().set_waiting_approval(
        _prepare_store(),
        str(owner_id),
        str(task_id),
        str(step_id),
        str(approval_token),
        now_iso,
    )
    _record("native_operations")
    return json.loads(raw)


def cancel(owner_id: str, task_id: str) -> dict:
    _, now_iso = _now_values()
    raw = _native().request_cancel(
        _prepare_store(), str(owner_id), str(task_id), now_iso
    )
    _record("native_operations")
    return json.loads(raw)


def finalize(owner_id: str, task_id: str, *, success: bool, error: str | None) -> dict:
    _, now_iso = _now_values()
    raw = _native().finalize_task(
        _prepare_store(),
        str(owner_id),
        str(task_id),
        bool(success),
        error,
        now_iso,
    )
    _record("native_operations")
    return json.loads(raw)
