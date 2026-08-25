"""Low-overhead latency measurements for the active conversation path."""

from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
import time


@dataclass(slots=True)
class RequestTrace:
    started_at: float = field(default_factory=time.perf_counter)
    stages_ms: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.stages_ms[name] = round((time.perf_counter() - started) * 1000, 2)

    def mark(self, name: str, started_at: float) -> None:
        self.stages_ms[name] = round((time.perf_counter() - started_at) * 1000, 2)

    def finish(self) -> dict[str, float]:
        result = dict(self.stages_ms)
        result["total"] = round((time.perf_counter() - self.started_at) * 1000, 2)
        return result


class LatencyMetrics:
    """Bounded in-process latency samples suitable for health reporting."""

    def __init__(self, max_samples: int = 500):
        self.max_samples = max(1, int(max_samples))
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.max_samples)
        )
        self._lock = threading.Lock()

    def observe(self, timings: dict[str, float]) -> None:
        with self._lock:
            for name, value in timings.items():
                self._samples[name].append(float(value))

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            samples = {name: list(values) for name, values in self._samples.items()}
        result = {}
        for name, values in samples.items():
            ordered = sorted(values)
            result[name] = {
                "count": len(values),
                "avg_ms": round(sum(values) / len(values), 2),
                "p95_ms": round(
                    ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2
                ),
            }
        return result

    def reset(self) -> None:
        """Discard all samples without replacing the shared metrics object."""
        with self._lock:
            self._samples.clear()


latency_metrics = LatencyMetrics()


class OperationalMetrics:
    """Persist a privacy-safe per-instance snapshot for external monitoring."""

    def __init__(self, path: Path | None = None):
        instance = os.getenv("CURIE_INSTANCE", "default").strip().casefold()
        filename = (
            "telemetry.json"
            if instance == "default"
            else f"instance-{instance}-telemetry.json"
        )
        self.path = path or Path.home() / ".curie" / filename
        self._lock = threading.Lock()
        self._request_times: deque[float] = deque(maxlen=500)
        self._total_requests = 0
        self._errors = 0
        self._fallbacks = 0

    @staticmethod
    def _token_estimate(text: str) -> int:
        return max(1, int(len(text.split()) * 1.33)) if text else 0

    def record_request(
        self,
        *,
        timings: dict[str, float],
        prompt: str,
        response: str,
        model: str,
        role: str = "general",
        fallback: bool = False,
        queue_depth: int = 0,
        inference: dict | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            self._request_times.append(now)
            self._total_requests += 1
            self._fallbacks += int(fallback)
            recent = [stamp for stamp in self._request_times if now - stamp <= 60]
            output_tokens = self._token_estimate(response)
            model_seconds = max(0.001, timings.get("model_response", 0) / 1000)
            try:
                context_size = max(1, int(os.getenv("LLM_CONTEXT_SIZE", "2048")))
            except (TypeError, ValueError):
                context_size = 2048
            snapshot = {
                "updated_at": now,
                "requests_total": self._total_requests,
                "requests_per_minute": len(recent),
                "latency": latency_metrics.snapshot(),
                "last_model": model,
                "last_role": role,
                "prompt_tokens_estimate": self._token_estimate(prompt),
                "output_tokens_estimate": output_tokens,
                "tokens_per_second_estimate": round(output_tokens / model_seconds, 1),
                "context_used_percent_estimate": round(
                    min(100, self._token_estimate(prompt) / context_size * 100), 1
                ),
                "queue_depth": queue_depth,
                "inference": dict(inference or {}),
                "errors": self._errors,
                "fallbacks": self._fallbacks,
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            except OSError:
                # Monitoring must never break the response path.
                pass

    def record_error(self) -> None:
        with self._lock:
            self._errors += 1

    def reset(self, *, path: Path | None = None) -> None:
        """Reset counters and optionally redirect the snapshot destination."""
        with self._lock:
            self._request_times.clear()
            self._total_requests = 0
            self._errors = 0
            self._fallbacks = 0
            if path is not None:
                self.path = path


def reset_telemetry(*, path: Path | None = None) -> None:
    """Reset both shared telemetry collectors at a lifecycle boundary."""
    latency_metrics.reset()
    operational_metrics.reset(path=path)


operational_metrics = OperationalMetrics()
