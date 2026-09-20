"""Release-gate loading, evaluation, and explicit waiver handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


_OPERATORS = {"gte", "gt", "lte", "lt", "eq"}


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    metric: str
    actual: float | None
    threshold: float
    operator: str
    passed: bool
    blocking: bool
    waived: bool
    component: str
    dataset: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "actual": self.actual,
            "threshold": self.threshold,
            "operator": self.operator,
            "passed": self.passed,
            "blocking": self.blocking,
            "waived": self.waived,
            "component": self.component,
            "dataset": self.dataset,
        }


def load_gate_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if int(config.get("schema_version", 0)) != 2:
        raise ValueError("release gate schema_version must be 2")
    gates = config.get("gates")
    if not isinstance(gates, dict) or not gates:
        raise ValueError("release gates must be a non-empty object")
    required = {
        "metric",
        "calculation",
        "dataset",
        "threshold",
        "operator",
        "blocking",
        "component",
        "waiver_process",
    }
    for name, gate in gates.items():
        missing = required - set(gate)
        if missing:
            raise ValueError(f"gate {name} is missing: {', '.join(sorted(missing))}")
        if gate["operator"] not in _OPERATORS:
            raise ValueError(f"gate {name} has an unsupported operator")
        if not str(gate["dataset"]).strip() or not str(gate["component"]).strip():
            raise ValueError(f"gate {name} requires dataset and component ownership")
    return config


def _compare(actual: float, operator: str, threshold: float) -> bool:
    return {
        "gte": actual >= threshold,
        "gt": actual > threshold,
        "lte": actual <= threshold,
        "lt": actual < threshold,
        "eq": actual == threshold,
    }[operator]


def _active_waivers(config: Mapping[str, Any]) -> set[str]:
    now = datetime.now(timezone.utc)
    active: set[str] = set()
    for waiver in config.get("waivers", ()):
        if not isinstance(waiver, Mapping):
            continue
        try:
            expiry = datetime.fromisoformat(
                str(waiver["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError):
            continue
        if (
            expiry > now
            and str(waiver.get("approved_by", "")).strip()
            and str(waiver.get("reason", "")).strip()
        ):
            active.add(str(waiver.get("gate")))
    return active


def evaluate_gates(
    config: Mapping[str, Any], metrics: Mapping[str, float | int]
) -> list[GateResult]:
    """Evaluate every configured gate; a missing metric always fails closed."""
    waivers = _active_waivers(config)
    results = []
    for name, gate in config["gates"].items():
        metric = str(gate["metric"])
        raw = metrics.get(metric)
        actual = float(raw) if raw is not None else None
        threshold = float(gate["threshold"])
        passed = actual is not None and _compare(
            actual, str(gate["operator"]), threshold
        )
        waiver_forbidden = (
            "not waivable" in str(gate.get("waiver_process", "")).casefold()
        )
        results.append(
            GateResult(
                name=name,
                metric=metric,
                actual=actual,
                threshold=threshold,
                operator=str(gate["operator"]),
                passed=passed,
                blocking=bool(gate["blocking"]),
                waived=name in waivers and not waiver_forbidden,
                component=str(gate["component"]),
                dataset=str(gate["dataset"]),
            )
        )
    return results


def release_blocked(results: list[GateResult]) -> bool:
    return any(
        result.blocking and not result.passed and not result.waived
        for result in results
    )


__all__ = ["GateResult", "evaluate_gates", "load_gate_config", "release_blocked"]
