"""Owner-scoped cooperative cancellation for in-process execution plans."""

from __future__ import annotations

import asyncio
import threading


class CancellationRegistry:
    """Track active plan events without retaining completed conversations."""

    def __init__(self) -> None:
        self._events: dict[str, set[asyncio.Event]] = {}
        self._lock = threading.RLock()

    def register(self, owner_id: str, event: asyncio.Event) -> None:
        with self._lock:
            self._events.setdefault(str(owner_id), set()).add(event)

    def unregister(self, owner_id: str, event: asyncio.Event) -> None:
        with self._lock:
            events = self._events.get(str(owner_id))
            if not events:
                return
            events.discard(event)
            if not events:
                self._events.pop(str(owner_id), None)

    def cancel(self, owner_id: str) -> int:
        with self._lock:
            events = tuple(self._events.get(str(owner_id), ()))
        for event in events:
            event.set()
        return len(events)

    def active_count(self, owner_id: str) -> int:
        with self._lock:
            return len(self._events.get(str(owner_id), ()))


_registry = CancellationRegistry()


def get_cancellation_registry() -> CancellationRegistry:
    return _registry
