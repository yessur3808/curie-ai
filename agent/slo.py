"""Stage-level service objectives and privacy-safe latency dashboards."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import os
import threading
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class SLODefinition:
    name: str
    target_p95_ms: float
    minimum_success_rate: float = 0.99


def _target(name: str, default: int) -> float:
    try:
        return max(1.0, float(os.getenv(f"SLO_{name.upper()}_P95_MS", default)))
    except (TypeError, ValueError):
        return float(default)


SLO_DEFINITIONS = (
    SLODefinition("connector_receive", _target("connector_receive", 250)),
    SLODefinition("acknowledgement", _target("acknowledgement", 1000)),
    SLODefinition("context_build", _target("context_build", 1500)),
    SLODefinition("routing", _target("routing", 500)),
    SLODefinition("model_generation", _target("model_generation", 20000), 0.97),
    SLODefinition("tool_execution", _target("tool_execution", 30000), 0.97),
    SLODefinition("verification", _target("verification", 5000), 0.99),
    SLODefinition("connector_delivery", _target("connector_delivery", 3000), 0.99),
)


class SLOMetrics:
    def __init__(self, *, max_samples: int = 500):
        self._definitions = {item.name: item for item in SLO_DEFINITIONS}
        self._durations = defaultdict(lambda: deque(maxlen=max(1, max_samples)))
        self._successes = CounterLike()
        self._totals = CounterLike()
        self._lock = threading.RLock()

    def observe(self, name: str, duration_ms: float, *, success: bool = True) -> None:
        if name not in self._definitions:
            return
        with self._lock:
            self._durations[name].append(max(0.0, float(duration_ms)))
            self._totals[name] += 1
            self._successes[name] += int(success)

    def observe_pipeline(self, stages: Iterable[Mapping[str, object]]) -> None:
        by_name = {
            str(item.get("stage")): item for item in stages if isinstance(item, Mapping)
        }

        def duration(*names: str) -> float:
            return sum(
                float((by_name.get(name) or {}).get("duration_ms") or 0)
                for name in names
            )

        def success(*names: str) -> bool:
            return all(
                str((by_name.get(name) or {}).get("status") or "")
                not in {"failed", "cancelled"}
                for name in names
            )

        mappings = {
            "connector_receive": ("normalize",),
            "acknowledgement": ("resolve_identity",),
            "context_build": ("build_context",),
            "routing": ("understand", "route"),
            "tool_execution": ("execute",),
            "verification": ("verify",),
            "connector_delivery": ("deliver",),
        }
        for metric, names in mappings.items():
            if all(name in by_name for name in names):
                self.observe(metric, duration(*names), success=success(*names))

    def snapshot(self) -> dict[str, dict[str, float | int | str]]:
        with self._lock:
            durations = {name: list(values) for name, values in self._durations.items()}
            totals = dict(self._totals)
            successes = dict(self._successes)
        report = {}
        for name, definition in self._definitions.items():
            values = sorted(durations.get(name, ()))
            total = totals.get(name, 0)
            p50 = values[(len(values) - 1) // 2] if values else 0.0
            p95 = (
                values[min(len(values) - 1, int(len(values) * 0.95))] if values else 0.0
            )
            success_rate = successes.get(name, 0) / total if total else 0.0
            report[name] = {
                "count": total,
                "p50_ms": round(p50, 2),
                "p95_ms": round(p95, 2),
                "target_p95_ms": definition.target_p95_ms,
                "success_rate": round(success_rate, 4),
                "minimum_success_rate": definition.minimum_success_rate,
                "status": (
                    "no_data"
                    if not total
                    else (
                        "meeting"
                        if p95 <= definition.target_p95_ms
                        and success_rate >= definition.minimum_success_rate
                        else "breached"
                    )
                ),
            }
        return report

    def reset(self) -> None:
        with self._lock:
            self._durations.clear()
            self._totals.clear()
            self._successes.clear()


class CounterLike(defaultdict):
    def __init__(self):
        super().__init__(int)


slo_metrics = SLOMetrics()


__all__ = ["SLODefinition", "SLOMetrics", "SLO_DEFINITIONS", "slo_metrics"]
