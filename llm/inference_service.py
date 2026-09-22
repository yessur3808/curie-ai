"""Process-wide bounded, priority-aware inference coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
import os
import time
from typing import AsyncIterator, Awaitable, Callable
import uuid

TokenEmitter = Callable[[str], Awaitable[None]]
InferenceOperation = Callable[[TokenEmitter, asyncio.Event], Awaitable[str | None]]


class InferenceOverloaded(RuntimeError):
    """Raised when bounded inference capacity is exhausted."""


@dataclass(frozen=True, slots=True)
class InferenceResult:
    text: str
    request_id: str
    queue_ms: float
    first_token_ms: float
    total_ms: float
    output_tokens: int
    tokens_per_second: float


@dataclass(slots=True)
class _Job:
    request_id: str
    owner_id: str
    priority: int
    operation: InferenceOperation
    future: asyncio.Future[InferenceResult]
    created: float
    cancelled: asyncio.Event
    token_queue: asyncio.Queue[str | None]


class ManagedInferenceService:
    """Own all model work in this process and expose bounded fair scheduling."""

    PRIORITIES = {"active": 0, "interactive": 0, "background": 10}

    def __init__(self, *, capacity: int = 16, workers: int = 1):
        self.capacity = max(1, capacity)
        self.workers = max(1, workers)
        self._queue: asyncio.PriorityQueue[tuple[int, int, _Job]] = (
            asyncio.PriorityQueue(maxsize=self.capacity)
        )
        self._sequence = itertools.count()
        self._jobs: dict[str, _Job] = {}
        self._worker_tasks: list[asyncio.Task] = []
        self._metrics = {
            "submitted": 0,
            "completed": 0,
            "cancelled": 0,
            "overloaded": 0,
            "model_reloads": 0,
        }

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def snapshot(self) -> dict:
        queue_depth = self.queue_depth
        return {
            **self._metrics,
            "queue_depth": queue_depth,
            "queue_capacity": self.capacity,
            "queue_utilization_percent": round(queue_depth / self.capacity * 100, 1),
            "saturated": queue_depth >= self.capacity,
            "active_requests": len(self._jobs),
            "workers": self.workers,
        }

    def note_model_reload(self) -> None:
        self._metrics["model_reloads"] += 1

    def _ensure_workers(self) -> None:
        self._worker_tasks = [task for task in self._worker_tasks if not task.done()]
        while len(self._worker_tasks) < self.workers:
            self._worker_tasks.append(asyncio.create_task(self._worker()))

    async def submit(
        self,
        operation: InferenceOperation,
        *,
        owner_id: str,
        priority: str = "active",
        request_id: str | None = None,
        supersede_owner: bool = False,
    ) -> InferenceResult:
        self._ensure_workers()
        if priority not in self.PRIORITIES:
            raise ValueError(f"Unknown inference priority: {priority}")
        if supersede_owner:
            self.cancel_owner(owner_id)
        loop = asyncio.get_running_loop()
        job = _Job(
            request_id=request_id or uuid.uuid4().hex,
            owner_id=str(owner_id),
            priority=self.PRIORITIES[priority],
            operation=operation,
            future=loop.create_future(),
            created=time.perf_counter(),
            cancelled=asyncio.Event(),
            token_queue=asyncio.Queue(),
        )
        if job.request_id in self._jobs:
            raise ValueError("Inference request_id is already active")
        try:
            self._queue.put_nowait((job.priority, next(self._sequence), job))
        except asyncio.QueueFull as exc:
            self._metrics["overloaded"] += 1
            raise InferenceOverloaded("Inference queue is full") from exc
        self._jobs[job.request_id] = job
        self._metrics["submitted"] += 1
        try:
            return await job.future
        except asyncio.CancelledError:
            self.cancel(job.request_id)
            raise

    async def stream(self, *args, **kwargs) -> AsyncIterator[str]:
        """Yield emitted tokens while the same managed request is executing."""
        operation = args[0]
        kwargs = dict(kwargs)
        request_id = kwargs.setdefault("request_id", uuid.uuid4().hex)
        submit_task = asyncio.create_task(self.submit(operation, **kwargs))
        while request_id not in self._jobs and not submit_task.done():
            await asyncio.sleep(0)
        job = self._jobs.get(request_id)
        if job:
            while True:
                token = await job.token_queue.get()
                if token is None:
                    break
                yield token
        await submit_task

    def cancel(self, request_id: str) -> bool:
        job = self._jobs.get(request_id)
        if not job:
            return False
        job.cancelled.set()
        return True

    def cancel_owner(self, owner_id: str) -> int:
        jobs = [job for job in self._jobs.values() if job.owner_id == str(owner_id)]
        for job in jobs:
            job.cancelled.set()
        return len(jobs)

    async def close(self) -> None:
        for job in list(self._jobs.values()):
            job.cancelled.set()
        for task in self._worker_tasks:
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()

    async def _worker(self) -> None:
        while True:
            _, _, job = await self._queue.get()
            started = time.perf_counter()
            first_token_at: float | None = None
            chunks: list[str] = []

            async def emit(token: str) -> None:
                nonlocal first_token_at
                if job.cancelled.is_set():
                    raise asyncio.CancelledError
                if not token:
                    return
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                chunks.append(token)
                await job.token_queue.put(token)

            try:
                if job.cancelled.is_set():
                    raise asyncio.CancelledError
                returned = await job.operation(emit, job.cancelled)
                if returned and not chunks:
                    await emit(returned)
                text = "".join(chunks) if chunks else (returned or "")
                finished = time.perf_counter()
                first = first_token_at or finished
                generation_seconds = max(0.001, finished - first)
                token_count = max(0, int(len(text.split()) * 1.33))
                result = InferenceResult(
                    text=text,
                    request_id=job.request_id,
                    queue_ms=round((started - job.created) * 1000, 3),
                    first_token_ms=round((first - job.created) * 1000, 3),
                    total_ms=round((finished - job.created) * 1000, 3),
                    output_tokens=token_count,
                    tokens_per_second=round(token_count / generation_seconds, 2),
                )
                if not job.future.done():
                    job.future.set_result(result)
                self._metrics["completed"] += 1
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                self._metrics["cancelled"] += 1
            except Exception as exc:
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                await job.token_queue.put(None)
                self._jobs.pop(job.request_id, None)
                self._queue.task_done()


_service: ManagedInferenceService | None = None


def get_inference_service() -> ManagedInferenceService:
    global _service
    if _service is None:
        _service = ManagedInferenceService(
            capacity=int(os.getenv("INFERENCE_QUEUE_SIZE", "16")),
            workers=int(os.getenv("INFERENCE_WORKERS", "1")),
        )
    return _service


def reset_inference_service() -> None:
    global _service
    _service = None
