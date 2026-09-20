"""Versioned contracts for Curie's offline evaluation catalog and reports."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


CASE_SCHEMA_VERSION = "1.0"
_CASE_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+\.v[1-9][0-9]*$")
_RISKS = {"none", "read_only", "mutating", "sensitive", "critical"}
_ROLES = {"user", "assistant", "system", "tool"}


class EvaluationContractError(ValueError):
    """Raised when evaluation evidence does not satisfy its public contract."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationContractError(f"{field} must be an object")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvaluationContractError(f"{field} must be a list")
    return value


def validate_case(case: Mapping[str, Any], *, dataset_id: str = "") -> dict[str, Any]:
    """Validate one portable case without importing Curie's runtime."""
    required = {
        "id",
        "schema_version",
        "input_turns",
        "owner_profile",
        "provider_fixture",
        "expected",
        "tags",
        "risk",
    }
    missing = required - set(case)
    if missing:
        raise EvaluationContractError(
            f"{dataset_id or 'case'} is missing: {', '.join(sorted(missing))}"
        )
    case_id = str(case["id"])
    if not _CASE_ID.fullmatch(case_id):
        raise EvaluationContractError(f"invalid stable case id: {case_id}")
    if str(case["schema_version"]) != CASE_SCHEMA_VERSION:
        raise EvaluationContractError(f"unsupported case schema for {case_id}")
    turns = _list(case["input_turns"], f"{case_id}.input_turns")
    if not turns:
        raise EvaluationContractError(f"{case_id} requires at least one input turn")
    for index, turn in enumerate(turns):
        turn = _mapping(turn, f"{case_id}.input_turns[{index}]")
        if str(turn.get("role")) not in _ROLES or not str(turn.get("text", "")).strip():
            raise EvaluationContractError(f"invalid turn at {case_id}[{index}]")
    owner = _mapping(case["owner_profile"], f"{case_id}.owner_profile")
    if not str(owner.get("id", "")).strip():
        raise EvaluationContractError(f"{case_id} requires an owner fixture id")
    _mapping(case["provider_fixture"], f"{case_id}.provider_fixture")
    expected = _mapping(case["expected"], f"{case_id}.expected")
    expected_fields = {
        "route",
        "entities",
        "plan",
        "tool_calls",
        "verification",
        "response_facts",
        "forbidden_claims",
        "style",
    }
    absent = expected_fields - set(expected)
    if absent:
        raise EvaluationContractError(
            f"{case_id}.expected is missing: {', '.join(sorted(absent))}"
        )
    for name in ("route", "entities", "plan", "tool_calls", "verification", "style"):
        _mapping(expected[name], f"{case_id}.expected.{name}")
    _list(expected["response_facts"], f"{case_id}.expected.response_facts")
    _list(expected["forbidden_claims"], f"{case_id}.expected.forbidden_claims")
    tags = _list(case["tags"], f"{case_id}.tags")
    if not tags or any(not str(tag).strip() for tag in tags):
        raise EvaluationContractError(f"{case_id} requires non-empty tags")
    if str(case["risk"]) not in _RISKS:
        raise EvaluationContractError(f"invalid risk for {case_id}")
    return dict(case)


def load_dataset(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload = _mapping(payload, str(path))
    if str(payload.get("schema_version")) != CASE_SCHEMA_VERSION:
        raise EvaluationContractError(f"unsupported dataset schema: {path}")
    dataset_id = str(payload.get("dataset_id") or "")
    if not dataset_id:
        raise EvaluationContractError(f"dataset_id is required: {path}")
    raw_cases = _list(payload.get("cases"), f"{dataset_id}.cases")
    cases = [validate_case(case, dataset_id=dataset_id) for case in raw_cases]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise EvaluationContractError(f"duplicate case id in {dataset_id}")
    return {**dict(payload), "cases": cases}


def load_catalog(root: str | Path) -> list[dict[str, Any]]:
    """Load every versioned dataset, rejecting duplicate IDs across files."""
    root = Path(root)
    datasets = [load_dataset(path) for path in sorted(root.glob("*/*.json"))]
    seen: set[str] = set()
    for dataset in datasets:
        for case in dataset["cases"]:
            if case["id"] in seen:
                raise EvaluationContractError(
                    f"duplicate case id across datasets: {case['id']}"
                )
            seen.add(case["id"])
    return datasets


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """A diagnostic that is actionable without raw private conversation text."""

    case_id: str
    taxonomy: str
    stage: str
    expected: Mapping[str, Any]
    actual: Mapping[str, Any]
    model_version: str
    prompt_version: str
    feature_flags: tuple[str, ...]
    baseline_status: str
    minimal_reproduction: str
    owning_module: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "taxonomy": self.taxonomy,
            "stage": self.stage,
            "expected": dict(self.expected),
            "actual": dict(self.actual),
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "feature_flags": list(self.feature_flags),
            "baseline_status": self.baseline_status,
            "minimal_reproduction": self.minimal_reproduction,
            "owning_module": self.owning_module,
        }


def catalog_summary(datasets: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    datasets = list(datasets)
    cases = [case for dataset in datasets for case in dataset["cases"]]
    return {
        "dataset_count": len(datasets),
        "case_count": len(cases),
        "taxonomy_counts": {
            name: sum(name in case["tags"] for case in cases)
            for name in sorted({tag for case in cases for tag in case["tags"]})
        },
        "risk_counts": {
            risk: sum(case["risk"] == risk for case in cases)
            for risk in sorted({case["risk"] for case in cases})
        },
    }


__all__ = [
    "CASE_SCHEMA_VERSION",
    "EvaluationContractError",
    "FailureRecord",
    "catalog_summary",
    "load_catalog",
    "load_dataset",
    "validate_case",
]
