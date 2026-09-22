"""Bounded connector delivery gateway backed by Curie's Rust queue kernel.

Provider SDK calls stay in Python. The native kernel sees only opaque job IDs,
optional caller-supplied idempotency keys, timing, and queue policy; recipient
IDs and message content never cross this boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import threading
import time
from typing import Awaitable, Callable

_NATIVE_MODULE = None
_NATIVE_IMPORT_ATTEMPTED = False
_NATIVE_IMPORT_ERROR: str | None = None
_METRICS_LOCK = threading.Lock()
_METRICS = {
    "enqueued": 0,
    "completed": 0,
    "cancelled": 0,
    "expired": 0,
    "overloaded": 0,
    "duplicates": 0,
    "native_operations": 0,
    "python_operations": 0,
    "queue_wait_ms": 0.0,
}


class DeliveryGatewayError(RuntimeError):
    code = "delivery_gateway_error"


class DeliveryQueueOverloaded(DeliveryGatewayError):
    code = "delivery_queue_overloaded"


class DeliveryQueueExpired(DeliveryGatewayError):
    code = "delivery_queue_expired"


class DuplicateDelivery(DeliveryGatewayError):
    code = "duplicate_delivery"


@dataclass(frozen=True, slots=True)
class QueueExecution:
    value: object
    queue_wait_ms: float
    backend: str


def _mode() -> str:
    configured = os.getenv("CURIE_CONNECTOR_GATEWAY", "auto").strip().casefold()
    return configured if configured in {"auto", "rust", "python"} else "auto"


def _native_module():
    global _NATIVE_MODULE, _NATIVE_IMPORT_ATTEMPTED, _NATIVE_IMPORT_ERROR
    if _NATIVE_IMPORT_ATTEMPTED:
        return _NATIVE_MODULE
    _NATIVE_IMPORT_ATTEMPTED = True
    try:
        import _curie_connector_gateway as native

        _NATIVE_MODULE = native
        _NATIVE_IMPORT_ERROR = None
    except (ImportError, OSError) as exc:
        _NATIVE_MODULE = None
        _NATIVE_IMPORT_ERROR = type(exc).__name__
    return _NATIVE_MODULE


def connector_gateway_status() -> dict:
    mode = _mode()
    native = _native_module()
    available = native is not None
    version = None
    if available:
        try:
            version = str(native.gateway_version())
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


def connector_gateway_metrics(*, reset: bool = False) -> dict:
    with _METRICS_LOCK:
        result = dict(_METRICS)
        if reset:
            for key in _METRICS:
                _METRICS[key] = 0.0 if key == "queue_wait_ms" else 0
    return result


def _record(**values) -> None:
    with _METRICS_LOCK:
        for key, value in values.items():
            _METRICS[key] = _METRICS.get(key, 0) + value


class _PythonGateway:
    """Audited rollback with the same small state-machine contract as Rust."""

    def __init__(self, capacity: int, concurrency: int):
        if capacity < 1 or concurrency < 1 or concurrency > capacity:
            raise ValueError("invalid connector gateway bounds")
        self.capacity = capacity
        self.concurrency = concurrency
        self.next_id = 1
        self.pending: list[dict] = []
        self.active: set[int] = set()
        self.live_keys: dict[str, int] = {}
        self.rejected = 0
        self.expired = 0
        self.cancelled = 0
        self.completed = 0
        self.duplicates = 0
        self.lock = threading.RLock()

    def enqueue(self, *, idempotency_key=None, priority=0, deadline_ms=None):
        key = str(idempotency_key or "").strip() or None
        if key and len(key) > 128:
            raise ValueError("idempotency key exceeds 128 characters")
        with self.lock:
            if key and key in self.live_keys:
                self.duplicates += 1
                return "duplicate", self.live_keys[key]
            if len(self.pending) + len(self.active) >= self.capacity:
                self.rejected += 1
                return "overloaded", 0
            job_id = self.next_id
            self.next_id += 1
            self.pending.append(
                {
                    "id": job_id,
                    "sequence": job_id,
                    "priority": int(priority),
                    "deadline_ms": deadline_ms,
                    "key": key,
                }
            )
            if key:
                self.live_keys[key] = job_id
            return "accepted", job_id

    def try_claim(self, job_id: int, now_ms: int) -> str:
        with self.lock:
            if job_id in self.active:
                return "claimed"
            position = next(
                (
                    index
                    for index, item in enumerate(self.pending)
                    if item["id"] == job_id
                ),
                None,
            )
            if position is None:
                return "unknown"
            job = self.pending[position]
            if job["deadline_ms"] is not None and now_ms >= job["deadline_ms"]:
                self.pending.pop(position)
                if job["key"]:
                    self.live_keys.pop(job["key"], None)
                self.expired += 1
                return "expired"
            if len(self.active) >= self.concurrency:
                return "waiting"
            best = min(
                range(len(self.pending)),
                key=lambda index: (
                    -self.pending[index]["priority"],
                    self.pending[index]["sequence"],
                ),
            )
            if best != position:
                return "waiting"
            self.pending.pop(position)
            self.active.add(job_id)
            return "claimed"

    def _finish(self, job_id: int, *, cancelled: bool) -> bool:
        with self.lock:
            found = job_id in self.active
            self.active.discard(job_id)
            position = next(
                (
                    index
                    for index, item in enumerate(self.pending)
                    if item["id"] == job_id
                ),
                None,
            )
            if position is not None:
                self.pending.pop(position)
                found = True
            key = next(
                (key for key, value in self.live_keys.items() if value == job_id), None
            )
            if key:
                self.live_keys.pop(key, None)
            if found:
                if cancelled:
                    self.cancelled += 1
                else:
                    self.completed += 1
            return found

    def complete(self, job_id: int) -> bool:
        return self._finish(job_id, cancelled=False)

    def cancel(self, job_id: int) -> bool:
        return self._finish(job_id, cancelled=True)

    def snapshot(self):
        with self.lock:
            return (
                len(self.pending),
                len(self.active),
                self.capacity,
                self.concurrency,
                len(self.pending) + len(self.active) >= self.capacity,
                self.rejected,
                self.expired,
                self.cancelled,
                self.completed,
                self.duplicates,
            )


def _python_retry_delay(
    attempt: int,
    *,
    base_ms: int = 250,
    cap_ms: int = 30_000,
    retry_after_ms: int | None = None,
    jitter_seed: int = 0,
) -> int:
    if retry_after_ms is not None:
        return min(max(0, int(retry_after_ms)), max(1, int(cap_ms)))
    ceiling = min(
        max(1, int(cap_ms)), max(1, int(base_ms)) * 2 ** min(20, max(0, attempt - 1))
    )
    value = (int(jitter_seed) ^ (int(attempt) * 0x9E3779B97F4A7C15)) & ((1 << 64) - 1)
    value ^= value >> 12
    value ^= (value << 25) & ((1 << 64) - 1)
    value ^= value >> 27
    return (value * 0x2545F4914F6CDD1D & ((1 << 64) - 1)) % (ceiling + 1)


def retry_delay_ms(
    attempt: int,
    *,
    base_ms: int = 250,
    cap_ms: int = 30_000,
    retry_after_ms: int | None = None,
    jitter_seed: int = 0,
) -> int:
    native = _native_module()
    if _mode() != "python" and native is not None:
        return int(
            native.retry_delay_ms(
                int(attempt),
                base_ms=int(base_ms),
                cap_ms=int(cap_ms),
                retry_after_ms=retry_after_ms,
                jitter_seed=int(jitter_seed),
            )
        )
    if _mode() == "rust":
        raise RuntimeError(
            "CURIE_CONNECTOR_GATEWAY=rust but the native module is unavailable"
        )
    return _python_retry_delay(
        attempt,
        base_ms=base_ms,
        cap_ms=cap_ms,
        retry_after_ms=retry_after_ms,
        jitter_seed=jitter_seed,
    )


class DeliveryQueue:
    """Cross-event-loop bounded queue with native ordering and admission."""

    def __init__(self, capacity: int = 64, concurrency: int | None = None):
        self.capacity = max(1, int(capacity))
        configured = (
            int(os.getenv("CONNECTOR_DELIVERY_CONCURRENCY", "4"))
            if concurrency is None
            else int(concurrency)
        )
        self.concurrency = max(1, min(self.capacity, configured))
        mode = _mode()
        native = _native_module()
        if mode == "rust" and native is None:
            raise RuntimeError(
                "CURIE_CONNECTOR_GATEWAY=rust but the native module is unavailable"
            )
        if mode != "python" and native is not None:
            self._gateway = native.DeliveryGateway(self.capacity, self.concurrency)
            self.backend = "rust"
        else:
            self._gateway = _PythonGateway(self.capacity, self.concurrency)
            self.backend = "python"

    @property
    def size(self) -> int:
        snapshot = self._gateway.snapshot()
        return int(snapshot[0]) + int(snapshot[1])

    async def run_with_metadata(
        self,
        operation: Callable[[], Awaitable],
        *,
        idempotency_key: str | None = None,
        priority: int = 0,
        timeout_seconds: float | None = None,
    ) -> QueueExecution:
        submitted_ms = int(time.monotonic() * 1000)
        deadline_ms = (
            submitted_ms + max(1, int(float(timeout_seconds) * 1000))
            if timeout_seconds is not None
            else None
        )
        status, job_id = self._gateway.enqueue(
            idempotency_key=idempotency_key,
            priority=max(-32_768, min(32_767, int(priority))),
            deadline_ms=deadline_ms,
        )
        if status == "overloaded":
            _record(overloaded=1)
            raise DeliveryQueueOverloaded("connector work queue is full")
        if status == "duplicate":
            _record(duplicates=1)
            raise DuplicateDelivery("delivery idempotency key is already in flight")
        _record(enqueued=1)
        claimed = False
        try:
            while True:
                claim = self._gateway.try_claim(
                    int(job_id), int(time.monotonic() * 1000)
                )
                if claim == "claimed":
                    claimed = True
                    break
                if claim in {"expired", "unknown"}:
                    _record(expired=1)
                    raise DeliveryQueueExpired("connector delivery deadline expired")
                await asyncio.sleep(0.001)
            queue_wait_ms = max(0.0, time.monotonic() * 1000 - submitted_ms)
            _record(
                queue_wait_ms=queue_wait_ms,
                **{f"{self.backend}_operations": 1},
            )
            value = await operation()
            self._gateway.complete(int(job_id))
            _record(completed=1)
            return QueueExecution(value, round(queue_wait_ms, 3), self.backend)
        except asyncio.CancelledError:
            self._gateway.cancel(int(job_id))
            _record(cancelled=1)
            raise
        except Exception:
            if claimed:
                self._gateway.complete(int(job_id))
            else:
                self._gateway.cancel(int(job_id))
            raise

    async def run(self, operation: Callable[[], Awaitable], **kwargs):
        return (await self.run_with_metadata(operation, **kwargs)).value

    def snapshot(self) -> dict[str, int | bool | str]:
        values = self._gateway.snapshot()
        return {
            "pending": int(values[0]),
            "active": int(values[1]),
            "size": int(values[0]) + int(values[1]),
            "capacity": int(values[2]),
            "concurrency": int(values[3]),
            "saturated": bool(values[4]),
            "rejected": int(values[5]),
            "expired": int(values[6]),
            "cancelled": int(values[7]),
            "completed": int(values[8]),
            "duplicates": int(values[9]),
            "backend": self.backend,
        }
