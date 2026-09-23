"""Typed bridge to Curie's unified Rust runtime kernel.

The native boundary is deliberately coarse: stateful SQLite access, ingress
admission, deterministic text processing, model residency accounting,
redaction/telemetry batching, and bounded document bytes.  Business policy and
conversation decisions remain in Python.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

_NATIVE_MODULE = None
_NATIVE_IMPORT_ATTEMPTED = False
_NATIVE_IMPORT_ERROR: str | None = None
_LOCK = threading.RLock()
_STORES: dict[str, Any] = {}
_INGRESS: dict[str, Any] = {}
_TELEMETRY = None
_MODEL_SUPERVISOR = None
_TRANSACTION_STATE = threading.local()
_METRICS = {
    "persistence_operations": 0,
    "ingress_admissions": 0,
    "ingress_duplicates": 0,
    "language_operations": 0,
    "document_operations": 0,
    "redaction_operations": 0,
}


def _mode() -> str:
    value = os.getenv("CURIE_RUNTIME_KERNEL", "auto").strip().casefold()
    return value if value in {"auto", "rust", "python"} else "auto"


def _native_module():
    global _NATIVE_MODULE, _NATIVE_IMPORT_ATTEMPTED, _NATIVE_IMPORT_ERROR
    if _NATIVE_IMPORT_ATTEMPTED:
        return _NATIVE_MODULE
    _NATIVE_IMPORT_ATTEMPTED = True
    try:
        import _curie_runtime_kernel as native

        _NATIVE_MODULE = native
        _NATIVE_IMPORT_ERROR = None
    except (ImportError, OSError) as exc:
        _NATIVE_MODULE = None
        _NATIVE_IMPORT_ERROR = type(exc).__name__
    return _NATIVE_MODULE


def runtime_kernel_status() -> dict:
    mode = _mode()
    native = _native_module()
    available = native is not None
    version = None
    if available:
        try:
            version = str(native.runtime_version())
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
        "metrics": dict(_METRICS),
    }


def use_native() -> bool:
    status = runtime_kernel_status()
    if status["mode"] == "rust" and status["active"] != "rust":
        raise RuntimeError(
            "CURIE_RUNTIME_KERNEL=rust but the native module is unavailable"
        )
    return status["active"] == "rust"


def _native():
    if not use_native():
        raise RuntimeError("The Rust runtime kernel is not active")
    return _native_module()


class NativeCursor:
    def __init__(self, result: dict):
        self._rows = list(result.get("rows", []))
        self.rowcount = int(result.get("rowcount", 0))
        self._index = 0

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        value = self._rows[self._index]
        self._index += 1
        return value

    def fetchall(self):
        values = self._rows[self._index :]
        self._index = len(self._rows)
        return values


class NativeConnection:
    """Small DB-API compatibility surface used by ``memory.local_store``."""

    def __init__(self, store):
        self._store = store

    def execute(self, sql: str, parameters=()):
        raw = self._store.execute(
            str(sql), json.dumps(list(parameters), separators=(",", ":"), default=str)
        )
        _METRICS["persistence_operations"] += 1
        return NativeCursor(json.loads(raw))

    def __enter__(self):
        depth = int(getattr(_TRANSACTION_STATE, "depth", 0))
        if depth == 0:
            self._store.begin()
        _TRANSACTION_STATE.depth = depth + 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        depth = max(0, int(getattr(_TRANSACTION_STATE, "depth", 1)) - 1)
        _TRANSACTION_STATE.depth = depth
        if depth == 0:
            if exc_type is None:
                self._store.commit()
            else:
                self._store.rollback()
        return False

    def close(self):
        # The process-resident connection intentionally stays warm.
        return None

    def commit(self):
        # local_store has a small number of DB-API-compatible helper calls
        # inside an outer managed transaction. Do not prematurely commit it.
        if int(getattr(_TRANSACTION_STATE, "depth", 0)) == 0:
            self._store.commit()

    def rollback(self):
        if int(getattr(_TRANSACTION_STATE, "depth", 0)) == 0:
            self._store.rollback()


def persistence_store(path: str | os.PathLike):
    resolved = str(Path(path).resolve())
    with _LOCK:
        store = _STORES.get(resolved)
        if store is None:
            store = _native().PersistenceStore(resolved)
            _STORES[resolved] = store
        return store


def persistence_connection(path: str | os.PathLike) -> NativeConnection:
    return NativeConnection(persistence_store(path))


def reset_persistence(path: str | os.PathLike) -> None:
    """Drop process-resident handles before a controlled file replacement."""
    resolved = str(Path(path).resolve())
    with _LOCK:
        _INGRESS.pop(resolved, None)
        _STORES.pop(resolved, None)


def append_event(
    path: str | os.PathLike,
    stream: str,
    event_type: str,
    payload: Any,
    *,
    owner_id: str | None = None,
    dedupe_key: str | None = None,
    created_at_ms: int | None = None,
) -> int:
    store = persistence_store(path)
    return int(
        store.append_event(
            stream,
            event_type,
            owner_id,
            json.dumps(payload, separators=(",", ":"), default=str),
            int(created_at_ms or time.time() * 1000),
            dedupe_key,
        )
    )


def read_events(
    path: str | os.PathLike, stream: str, *, after_sequence: int = 0, limit: int = 100
) -> list[dict]:
    return json.loads(
        persistence_store(path).read_events(stream, int(after_sequence), int(limit))
    )


def preprocess_text(value: str) -> dict | None:
    if not use_native():
        return None
    _METRICS["language_operations"] += 1
    return json.loads(_native().preprocess_text(value))


def _ingress_gateway(path: str | os.PathLike):
    resolved = str(Path(path).resolve())
    with _LOCK:
        gateway = _INGRESS.get(resolved)
        if gateway is None:
            gateway = _native().IngressGateway(
                resolved,
                int(os.getenv("CURIE_INGRESS_TTL_MS", "600000")),
                int(os.getenv("CURIE_INGRESS_MAX_ENTRIES", "50000")),
            )
            _INGRESS[resolved] = gateway
        return gateway


def admit_ingress(
    path: str | os.PathLike,
    connector: str,
    dedupe_key: str,
    *,
    now_ms: int | None = None,
) -> bool | None:
    if not use_native():
        return None
    admitted = bool(
        _ingress_gateway(path).admit(
            str(connector), str(dedupe_key), int(now_ms or time.time() * 1000)
        )
    )
    _METRICS["ingress_admissions" if admitted else "ingress_duplicates"] += 1
    return admitted


def store_ingress_response(
    path: str | os.PathLike, dedupe_key: str, response: str
) -> bool:
    if not use_native():
        return False
    return bool(_ingress_gateway(path).store_response(str(dedupe_key), str(response)))


def ingress_response(
    path: str | os.PathLike, dedupe_key: str, *, now_ms: int | None = None
) -> str | None:
    if not use_native():
        return None
    return _ingress_gateway(path).response(
        str(dedupe_key), int(now_ms or time.time() * 1000)
    )


def model_supervisor():
    global _MODEL_SUPERVISOR
    if not use_native():
        return None
    mode = os.getenv("CURIE_MODEL_SUPERVISOR", "profiled").strip().casefold()
    if mode not in {"rust", "on", "enabled"}:
        return None
    with _LOCK:
        if _MODEL_SUPERVISOR is None:
            _MODEL_SUPERVISOR = _native().ModelSupervisor(
                int(os.getenv("LLM_MAX_LOADED_MODELS", "2")),
                int(os.getenv("LLM_MAX_RESIDENT_BYTES", "0")),
            )
    return _MODEL_SUPERVISOR


def model_supervisor_status() -> dict:
    mode = os.getenv("CURIE_MODEL_SUPERVISOR", "profiled").strip().casefold()
    active = mode in {"rust", "on", "enabled"} and use_native()
    supervisor = model_supervisor() if active else None
    return {
        "mode": mode,
        "active": "rust" if supervisor is not None else "profiled_off",
        "profile_required": supervisor is None,
        "snapshot": (
            json.loads(supervisor.snapshot()) if supervisor is not None else None
        ),
    }


def redact_native(value: Any) -> Any | None:
    if not use_native():
        return None
    _METRICS["redaction_operations"] += 1
    raw = json.dumps(value, separators=(",", ":"), default=str)
    return json.loads(_native().redact_json(raw))


def telemetry_buffer():
    global _TELEMETRY
    if not use_native():
        return None
    with _LOCK:
        if _TELEMETRY is None:
            _TELEMETRY = _native().TelemetryBuffer(
                int(os.getenv("CURIE_TELEMETRY_BUFFER_SIZE", "4096"))
            )
    return _TELEMETRY


def record_telemetry(event: dict) -> bool:
    buffer = telemetry_buffer()
    if buffer is None:
        return False
    buffer.push(json.dumps(event, separators=(",", ":"), default=str))
    return True


def drain_telemetry(limit: int = 256) -> list[dict]:
    buffer = telemetry_buffer()
    if buffer is None:
        return []
    return json.loads(buffer.drain(int(limit)))


def flush_telemetry(
    path: str | os.PathLike, *, max_bytes: int, limit: int = 256
) -> int:
    buffer = telemetry_buffer()
    if buffer is None:
        return 0
    return int(buffer.flush_jsonl(str(path), int(max_bytes), int(limit)))


def extract_document_native(
    path: str,
    suffix: str,
    *,
    max_source_bytes: int,
    max_expanded_bytes: int,
    max_chars: int,
    max_members: int = 2048,
) -> str | None:
    if not use_native():
        return None
    _METRICS["document_operations"] += 1
    return str(
        _native().extract_document(
            path,
            suffix,
            int(max_source_bytes),
            int(max_expanded_bytes),
            int(max_chars),
            int(max_members),
        )
    )


@contextmanager
def forced_python_runtime():
    """Test helper for audited rollback behavior."""
    old = os.environ.get("CURIE_RUNTIME_KERNEL")
    os.environ["CURIE_RUNTIME_KERNEL"] = "python"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CURIE_RUNTIME_KERNEL", None)
        else:
            os.environ["CURIE_RUNTIME_KERNEL"] = old
