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
from typing import Any, Mapping

from agent.slo import slo_metrics
from utils.redaction import redact_secrets


TRACE_SCHEMA_VERSION = 2
TRACE_REQUIRED_FIELDS = frozenset(
    {
        "trace_id",
        "turn_id",
        "owner_scope_hash",
        "connector",
        "stage",
        "duration_ms",
        "route",
        "confidence",
        "capability",
        "outcome_category",
        "verification_category",
        "feature_flags",
        "model_version",
        "prompt_version",
        "token_counts",
        "queue_wait_ms",
    }
)


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
        if "context" in timings:
            slo_metrics.observe("context_build", timings["context"])
        if "model_response" in timings:
            slo_metrics.observe(
                "model_generation", timings["model_response"], success=True
            )
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
                "slos": slo_metrics.snapshot(),
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


class TurnEventWriter:
    """Append privacy-safe end-to-end turn events for evaluation and debugging."""

    def __init__(self, path: Path | None = None, *, max_bytes: int | None = None):
        instance = os.getenv("CURIE_INSTANCE", "default").strip().casefold()
        filename = (
            "turn-events.jsonl"
            if instance == "default"
            else f"instance-{instance}-turn-events.jsonl"
        )
        configured = os.getenv("CURIE_TURN_TRACE_PATH", "").strip()
        self.path = path or (
            Path(configured) if configured else Path.home() / ".curie" / filename
        )
        try:
            configured_max = int(os.getenv("CURIE_TURN_TRACE_MAX_BYTES", "10000000"))
        except ValueError:
            configured_max = 10_000_000
        self.max_bytes = max(64_000, int(max_bytes or configured_max))
        self._lock = threading.Lock()

    def _rotate_if_needed(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
            rotated = self.path.with_suffix(self.path.suffix + ".1")
            self.path.replace(rotated)
        except OSError:
            pass

    def _append(self, event: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            redact_secrets(event), separators=(",", ":"), sort_keys=True
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
            self.path.chmod(0o600)

    def record(self, state: Any, response: Mapping[str, Any]) -> None:
        """Write metadata only: never raw prompts, responses, IDs, or parameters."""
        try:
            subgoals = tuple(getattr(state.goal, "subgoals", ()))
            capabilities = [
                item.capability
                for item in subgoals
                if getattr(item, "capability", None)
            ]
            text = str(response.get("text") or "")
            event = {
                "schema_version": 1,
                "recorded_at": time.time(),
                "trace_id": state.trace_id,
                "turn_id": state.id,
                "platform": state.platform,
                "owner_scope_hash": state.owner_scope_hash,
                "message_hash": state.message_hash,
                "message_chars": state.original_length,
                "intent": state.goal.intent,
                "capabilities": capabilities,
                "risk": state.goal.risk,
                "response_mode": state.response_mode.value,
                "memory_policy": state.memory_policy,
                "entity_count": len(state.entities),
                "entity_kinds": sorted({item.kind for item in state.entities}),
                "model_used": str(response.get("model_used") or "unknown"),
                "timings_ms": {
                    str(key): float(value)
                    for key, value in dict(response.get("timings_ms") or {}).items()
                    if isinstance(value, (int, float))
                },
                "processing_time_ms": float(response.get("processing_time_ms") or 0),
                "response_chars": len(text),
                "outcome": (
                    "error"
                    if text.startswith("[Error") or response.get("model_used") == "N/A"
                    else "completed"
                ),
            }
            pipeline = response.get("pipeline")
            if isinstance(pipeline, Mapping):
                stages = pipeline.get("stages")
                event.update(
                    {
                        "pipeline_mode": str(pipeline.get("mode") or "unknown"),
                        "pipeline_failed_stage": pipeline.get("failed_stage"),
                        "pipeline_stage_statuses": {
                            str(item.get("stage")): str(item.get("status"))
                            for item in stages or ()
                            if isinstance(item, Mapping)
                        },
                    }
                )
            event["response_origin"] = str(
                response.get("response_origin") or "legacy_chat_workflow"
            )
            self._append(event)
        except Exception:
            # Observability must never alter the conversational outcome.
            pass

    def record_pipeline(
        self,
        state: Any,
        *,
        response: Mapping[str, Any] | None = None,
        comparison: Mapping[str, Any] | None = None,
    ) -> None:
        """Write an allowlisted stage trace without serializing stage artifacts."""
        try:
            safe = state.as_dict()
            stages = tuple(safe.get("stages") or ())
            stage_map = {
                str(item.get("stage")): item
                for item in stages
                if isinstance(item, Mapping)
            }

            def summary(stage: str) -> Mapping[str, Any]:
                value = (stage_map.get(stage) or {}).get("summary") or {}
                return value if isinstance(value, Mapping) else {}

            understanding = summary("understand")
            routing = summary("route")
            execution = summary("execute")
            verification = summary("verify")
            response_plan = summary("plan_response")
            telemetry = (
                response.get("telemetry", {})
                if isinstance(response, Mapping)
                and isinstance(response.get("telemetry"), Mapping)
                else {}
            )
            capabilities = [
                str(item) for item in routing.get("capabilities", ()) if item
            ]
            slo_metrics.observe_pipeline(stages)
            event: dict[str, Any] = {
                "schema_version": TRACE_SCHEMA_VERSION,
                "event_type": "pipeline_trace",
                "recorded_at": time.time(),
                "trace_id": str(safe.get("trace_id") or ""),
                "turn_id": str(safe.get("turn_id") or ""),
                "stage": str(safe.get("failed_stage") or "complete"),
                "connector": str(safe.get("platform") or "unknown"),
                "platform": str(safe.get("platform") or "unknown"),
                "owner_scope_hash": str(safe.get("owner_scope_hash") or ""),
                "message_hash": str(safe.get("message_hash") or ""),
                "message_chars": int(safe.get("message_chars") or 0),
                "pipeline_mode": str(safe.get("mode") or "unknown"),
                "feature_flags": {
                    "turn_pipeline_mode": str(safe.get("mode") or "unknown")
                },
                "pipeline_failed_stage": safe.get("failed_stage"),
                "duration_ms": round(
                    sum(
                        float(item.get("duration_ms") or 0)
                        for item in stages
                        if isinstance(item, Mapping)
                    ),
                    2,
                ),
                "route": str(
                    routing.get("route") or understanding.get("intent") or "unknown"
                ),
                "confidence": float(understanding.get("recognizer_confidence") or 0.0),
                "capability": capabilities[0] if capabilities else None,
                "capabilities": capabilities,
                "outcome_category": str(response_plan.get("outcome") or "unknown"),
                "verification_category": str(
                    verification.get("verification_status") or "not_recorded"
                ),
                "model_version": str(
                    (
                        response.get("model_used")
                        if isinstance(response, Mapping)
                        else None
                    )
                    or execution.get("model_family")
                    or "unknown"
                ),
                "prompt_version": str(
                    os.getenv("CURIE_PROMPT_VERSION", "repository-current")
                )[:120],
                "token_counts": {
                    "prompt": int(telemetry.get("prompt_tokens") or 0),
                    "output": int(telemetry.get("output_tokens") or 0),
                },
                "queue_wait_ms": float(telemetry.get("queue_wait_ms") or 0),
                "pipeline_stage_statuses": {
                    str(item.get("stage")): str(item.get("status"))
                    for item in stages
                    if isinstance(item, Mapping)
                },
                "pipeline_stage_durations_ms": {
                    str(item.get("stage")): float(item.get("duration_ms") or 0)
                    for item in stages
                    if isinstance(item, Mapping)
                },
                "slo_snapshot": slo_metrics.snapshot(),
            }
            if response is not None:
                event.update(
                    {
                        "response_origin": str(
                            response.get("response_origin") or "unknown"
                        ),
                        "model_used": str(response.get("model_used") or "unknown"),
                        "response_chars": len(str(response.get("text") or "")),
                    }
                )
            if comparison is not None:
                event["shadow_comparison"] = {
                    "route_match": bool(comparison.get("route_match")),
                    "expected_capability_count": int(
                        comparison.get("expected_capability_count") or 0
                    ),
                    "legacy_capability_count": int(
                        comparison.get("legacy_capability_count") or 0
                    ),
                    "legacy_response_present": bool(
                        comparison.get("legacy_response_present")
                    ),
                }
            self._append(event)
        except Exception:
            # Observability must never alter the conversational outcome.
            pass


turn_event_writer = TurnEventWriter()


__all__ = [
    "TRACE_REQUIRED_FIELDS",
    "TRACE_SCHEMA_VERSION",
    "LatencyMetrics",
    "OperationalMetrics",
    "RequestTrace",
    "TurnEventWriter",
    "latency_metrics",
    "operational_metrics",
    "reset_telemetry",
    "turn_event_writer",
]
