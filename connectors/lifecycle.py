"""Shared lifecycle, health, and bounded-delivery primitives for connectors."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import logging
import threading
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class ConnectorState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class DeliveryStatus(str, Enum):
    DELIVERED = "delivered"
    REJECTED = "rejected"
    NOT_READY = "not_ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    name: str
    state: ConnectorState
    ready: bool
    thread_alive: bool
    queue_size: int
    queue_capacity: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    connector: str
    delivered: bool
    status: DeliveryStatus
    duration_ms: float
    error_type: str | None = None

    def as_dict(self) -> dict[str, str | bool | float | None]:
        return {
            "connector": self.connector,
            "delivered": self.delivered,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "error_type": self.error_type,
        }


class BoundedAsyncWorkQueue:
    """Bound concurrent async work and reject overload instead of growing forever."""

    def __init__(self, capacity: int = 64):
        self.capacity = max(1, int(capacity))
        self._slots = threading.BoundedSemaphore(self.capacity)
        self._active = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        with self._lock:
            return self._active

    async def run(self, operation: Callable[[], Awaitable]):
        if not self._slots.acquire(blocking=False):
            raise RuntimeError("connector work queue is full")
        with self._lock:
            self._active += 1
        try:
            return await operation()
        finally:
            with self._lock:
                self._active -= 1
            self._slots.release()


class ConnectorApplication:
    """Explicit application object around a blocking connector runtime."""

    def __init__(
        self,
        name: str,
        start_fn: Callable,
        *,
        send_fn: Callable | None = None,
        stop_fn: Callable | None = None,
        ready_probe: Callable[[], bool] | None = None,
        queue_capacity: int = 64,
    ):
        self.name = name
        self.start_fn = start_fn
        self.send_fn = send_fn
        self.stop_fn = stop_fn
        self.ready_probe = ready_probe
        self.state = ConnectorState.CREATED
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self.ready_event = threading.Event()
        self.outbound_queue = BoundedAsyncWorkQueue(queue_capacity)
        self.last_delivery_receipt: DeliveryReceipt | None = None

    def start(self, workflow) -> threading.Thread:
        if self.thread and self.thread.is_alive():
            return self.thread
        self.state = ConnectorState.STARTING
        self.error = None

        def runner():
            try:
                self.start_fn(workflow)
                if self.state not in {ConnectorState.STOPPING, ConnectorState.STOPPED}:
                    self.state = ConnectorState.STOPPED
            except Exception as exc:
                self.error = str(exc)
                self.state = ConnectorState.FAILED
                logger.exception("Connector %s failed", self.name)

        self.thread = threading.Thread(
            target=runner, name=f"curie-{self.name}", daemon=True
        )
        self.thread.start()
        return self.thread

    def refresh_readiness(self) -> bool:
        if self.state in {
            ConnectorState.FAILED,
            ConnectorState.STOPPING,
            ConnectorState.STOPPED,
        }:
            return False
        ready = (
            bool(self.ready_probe())
            if self.ready_probe
            else bool(self.thread and self.thread.is_alive())
        )
        if ready:
            self.ready_event.set()
            self.state = ConnectorState.READY
        return ready

    def wait_ready(self, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            if self.refresh_readiness():
                return True
            if self.state == ConnectorState.FAILED:
                return False
            self.ready_event.wait(min(0.05, max(0.0, deadline - time.monotonic())))
        return self.refresh_readiness()

    async def send_with_receipt(self, recipient: str, message: str) -> DeliveryReceipt:
        """Attempt delivery and return truthful, privacy-safe outcome metadata."""
        started = time.perf_counter()

        def receipt(
            delivered: bool,
            status: DeliveryStatus,
            error_type: str | None = None,
        ) -> DeliveryReceipt:
            value = DeliveryReceipt(
                connector=self.name,
                delivered=delivered,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error_type=error_type,
            )
            self.last_delivery_receipt = value
            return value

        if self.send_fn is None:
            return receipt(False, DeliveryStatus.UNAVAILABLE)
        try:
            if not self.refresh_readiness():
                return receipt(False, DeliveryStatus.NOT_READY)
            delivered = bool(
                await self.outbound_queue.run(lambda: self.send_fn(recipient, message))
            )
            return receipt(
                delivered,
                DeliveryStatus.DELIVERED if delivered else DeliveryStatus.REJECTED,
            )
        except Exception as exc:
            logger.warning(
                "Connector %s delivery failed (%s)", self.name, type(exc).__name__
            )
            return receipt(False, DeliveryStatus.FAILED, type(exc).__name__)

    async def send(self, recipient: str, message: str) -> bool:
        return (await self.send_with_receipt(recipient, message)).delivered

    def stop(self) -> None:
        if self.state == ConnectorState.STOPPED:
            return
        self.state = ConnectorState.STOPPING
        if self.stop_fn:
            self.stop_fn()
        if self.thread:
            self.thread.join(timeout=5)
        self.state = ConnectorState.STOPPED
        self.ready_event.clear()

    def health(self) -> ConnectorHealth:
        if self.state not in {
            ConnectorState.FAILED,
            ConnectorState.STOPPING,
            ConnectorState.STOPPED,
        }:
            self.refresh_readiness()
        return ConnectorHealth(
            self.name,
            self.state,
            self.ready_event.is_set(),
            bool(self.thread and self.thread.is_alive()),
            self.outbound_queue.size,
            self.outbound_queue.capacity,
            self.error,
        )


class ConnectorRegistry:
    def __init__(self):
        self._connectors: dict[str, ConnectorApplication] = {}

    def register(self, connector: ConnectorApplication) -> None:
        if connector.name in self._connectors:
            raise ValueError(f"Connector {connector.name!r} is already registered")
        self._connectors[connector.name] = connector

    def get(self, name: str) -> ConnectorApplication:
        return self._connectors[name]

    def outbound(self) -> dict[str, Callable]:
        return {
            name: connector.send
            for name, connector in self._connectors.items()
            if connector.send_fn is not None
        }

    def outbound_with_receipts(self) -> dict[str, Callable]:
        return {
            name: connector.send_with_receipt
            for name, connector in self._connectors.items()
            if connector.send_fn is not None
        }

    def health(self) -> dict[str, ConnectorHealth]:
        return {
            name: connector.health() for name, connector in self._connectors.items()
        }

    def wait_ready(self, timeout: float = 15.0) -> dict[str, bool]:
        """Wait up to one shared deadline for all registered connectors."""
        deadline = time.monotonic() + max(0.0, timeout)
        return {
            name: connector.wait_ready(max(0.0, deadline - time.monotonic()))
            for name, connector in self._connectors.items()
        }

    def stop_all(self) -> None:
        for connector in self._connectors.values():
            connector.stop()
