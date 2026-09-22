"""Process-wide fail-fast resource limits for reads and owner mutations."""

from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
import os
import threading
from typing import AsyncIterator


class BackpressureRejected(RuntimeError):
    """Raised when accepting work would exceed a configured resource budget."""

    def __init__(self, resource: str):
        self.resource = resource
        super().__init__(
            f"{resource} capacity is full; retry shortly instead of queuing more work"
        )


class RuntimeBackpressure:
    def __init__(
        self,
        *,
        global_read_limit: int | None = None,
        per_owner_mutation_limit: int | None = None,
    ):
        self.global_read_limit = max(
            1,
            int(
                global_read_limit
                if global_read_limit is not None
                else os.getenv("GLOBAL_READ_CONCURRENCY_LIMIT", "16")
            ),
        )
        self.per_owner_mutation_limit = max(
            1,
            int(
                per_owner_mutation_limit
                if per_owner_mutation_limit is not None
                else os.getenv("OWNER_MUTATION_CONCURRENCY_LIMIT", "1")
            ),
        )
        self._active_reads = 0
        self._owner_mutations: Counter[str] = Counter()
        self._accepted = Counter()
        self._rejected = Counter()
        self._lock = threading.RLock()

    @asynccontextmanager
    async def tool_slot(self, *, owner_id: str, mutating: bool) -> AsyncIterator[None]:
        owner = str(owner_id or "anonymous")
        resource = "owner_mutation" if mutating else "global_read"
        with self._lock:
            if mutating:
                if self._owner_mutations[owner] >= self.per_owner_mutation_limit:
                    self._rejected[resource] += 1
                    raise BackpressureRejected(resource)
                self._owner_mutations[owner] += 1
            else:
                if self._active_reads >= self.global_read_limit:
                    self._rejected[resource] += 1
                    raise BackpressureRejected(resource)
                self._active_reads += 1
            self._accepted[resource] += 1
        try:
            yield
        finally:
            with self._lock:
                if mutating:
                    self._owner_mutations[owner] -= 1
                    if self._owner_mutations[owner] <= 0:
                        self._owner_mutations.pop(owner, None)
                else:
                    self._active_reads = max(0, self._active_reads - 1)

    def snapshot(self) -> dict:
        with self._lock:
            busiest_owner = max(self._owner_mutations.values(), default=0)
            reads_saturated = self._active_reads >= self.global_read_limit
            mutations_saturated = busiest_owner >= self.per_owner_mutation_limit
            return {
                "global_reads": {
                    "active": self._active_reads,
                    "limit": self.global_read_limit,
                    "saturated": reads_saturated,
                    "accepted": self._accepted["global_read"],
                    "rejected": self._rejected["global_read"],
                },
                "owner_mutations": {
                    "active_owners": len(self._owner_mutations),
                    "busiest_owner_active": busiest_owner,
                    "per_owner_limit": self.per_owner_mutation_limit,
                    "saturated": mutations_saturated,
                    "accepted": self._accepted["owner_mutation"],
                    "rejected": self._rejected["owner_mutation"],
                },
                "saturated": reads_saturated or mutations_saturated,
            }

    def reset(self) -> None:
        with self._lock:
            self._active_reads = 0
            self._owner_mutations.clear()
            self._accepted.clear()
            self._rejected.clear()


runtime_backpressure = RuntimeBackpressure()


__all__ = ["BackpressureRejected", "RuntimeBackpressure", "runtime_backpressure"]
