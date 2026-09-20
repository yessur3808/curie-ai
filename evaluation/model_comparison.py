"""Fair comparison contracts for local and hosted model candidates."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ComparisonConditions:
    prompt_version: str
    context_version: str
    schema_version: str
    decoding: Mapping[str, Any]
    hardware: str
    timeout_seconds: float
    dataset_ids: tuple[str, ...]

    def fingerprint(self) -> tuple:
        return (
            self.prompt_version,
            self.context_version,
            self.schema_version,
            tuple(sorted(self.decoding.items())),
            self.hardware,
            self.timeout_seconds,
            self.dataset_ids,
        )


@dataclass(frozen=True, slots=True)
class ModelRun:
    model: str
    conditions: ComparisonConditions
    cases: tuple[Mapping[str, Any], ...]


def summarize_run(run: ModelRun) -> dict[str, Any]:
    cases = list(run.cases)

    def avg(name: str) -> float:
        values = [float(case[name]) for case in cases if case.get(name) is not None]
        return round(mean(values), 4) if values else 0.0

    return {
        "model": run.model,
        "case_count": len(cases),
        "accuracy": avg("accuracy"),
        "latency_ms": avg("latency_ms"),
        "tokens": avg("tokens"),
        "schema_adherence": avg("schema_adherence"),
        "appropriate_abstention": avg("appropriate_abstention"),
        "hallucination_rate": avg("hallucination_rate"),
        "personality_score": avg("personality_score"),
        "memory_relevance": avg("memory_relevance"),
        "tool_selection_accuracy": avg("tool_selection_accuracy"),
    }


def compare_runs(runs: Iterable[ModelRun]) -> dict[str, Any]:
    """Reject comparisons that changed anything except the model itself."""
    runs = list(runs)
    if len(runs) < 2:
        raise ValueError("at least two model runs are required")
    fingerprint = runs[0].conditions.fingerprint()
    if any(run.conditions.fingerprint() != fingerprint for run in runs[1:]):
        raise ValueError("model comparison conditions are not identical")
    case_ids = tuple(str(case.get("case_id")) for case in runs[0].cases)
    for run in runs[1:]:
        if tuple(str(case.get("case_id")) for case in run.cases) != case_ids:
            raise ValueError("model comparison case ordering differs")
    return {
        "conditions": {
            "prompt_version": runs[0].conditions.prompt_version,
            "context_version": runs[0].conditions.context_version,
            "schema_version": runs[0].conditions.schema_version,
            "decoding": dict(runs[0].conditions.decoding),
            "hardware": runs[0].conditions.hardware,
            "timeout_seconds": runs[0].conditions.timeout_seconds,
            "dataset_ids": list(runs[0].conditions.dataset_ids),
        },
        "models": [summarize_run(run) for run in runs],
    }


def execute_comparison(
    models: Mapping[
        str, Callable[[Mapping[str, Any], ComparisonConditions], Mapping[str, Any]]
    ],
    cases: Iterable[Mapping[str, Any]],
    conditions: ComparisonConditions,
) -> dict[str, Any]:
    """Execute injected adapters under one immutable comparison contract."""
    frozen_cases = tuple(dict(case) for case in cases)
    runs = []
    for model, adapter in models.items():
        observations = tuple(adapter(case, conditions) for case in frozen_cases)
        runs.append(ModelRun(model, conditions, observations))
    return compare_runs(runs)


__all__ = [
    "ComparisonConditions",
    "ModelRun",
    "compare_runs",
    "execute_comparison",
    "summarize_run",
]
