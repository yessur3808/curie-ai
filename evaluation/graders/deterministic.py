"""Deterministic first-line graders for structured Curie observations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

_SECRET = re.compile(
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----|\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b|"
    r"\b(?:password|passcode|seed phrase|recovery phrase)\s*[:=]",
    re.I,
)


def _normal(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _names(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = value
    else:
        values = ()
    result = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("name") or item.get("capability") or item.get("target")
        if item:
            result.append(_normal(item))
    return result


@dataclass(frozen=True, slots=True)
class GradeResult:
    case_id: str
    taxonomy: str
    stage: str
    passed: bool
    failures: tuple[str, ...]
    checks: Mapping[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "taxonomy": self.taxonomy,
            "stage": self.stage,
            "passed": self.passed,
            "failures": list(self.failures),
            "checks": dict(self.checks),
        }


def grade_case(case: Mapping[str, Any], observation: Mapping[str, Any]) -> GradeResult:
    """Compare facts and control flow without asking a model to judge itself."""
    expected = case["expected"]
    failures: list[str] = []
    checks: dict[str, bool] = {}

    def check(name: str, passed: bool, failure: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            failures.append(failure)

    wanted_route = expected["route"]
    actual_route = observation.get("route") or {}
    for key, value in wanted_route.items():
        if value in (None, "", []):
            continue
        check(
            f"route.{key}",
            _normal(actual_route.get(key)) == _normal(value),
            f"route {key} differs",
        )

    wanted_entities = _names(expected["entities"].get("targets", ()))
    actual_entities = _names((observation.get("entities") or {}).get("targets", ()))
    check(
        "entities",
        set(wanted_entities).issubset(actual_entities),
        "resolved entity set differs",
    )

    wanted_constraints = {
        _normal(item) for item in expected["plan"].get("required_constraints", ())
    }
    actual_constraints = {
        _normal(item) for item in (observation.get("plan") or {}).get("constraints", ())
    }
    check(
        "plan.constraints",
        wanted_constraints.issubset(actual_constraints),
        "required plan constraints are absent",
    )

    actual_tools = set(_names(observation.get("tool_calls", ())))
    required_tools = {
        _normal(item) for item in expected["tool_calls"].get("required", ())
    }
    forbidden_tools = {
        _normal(item) for item in expected["tool_calls"].get("forbidden", ())
    }
    check(
        "tools.required",
        required_tools.issubset(actual_tools),
        "required tool call is absent",
    )
    check(
        "tools.forbidden",
        not (forbidden_tools & actual_tools),
        "forbidden tool call occurred",
    )

    wanted_verification = _normal(expected["verification"].get("status"))
    actual_verification = _normal((observation.get("verification") or {}).get("status"))
    if wanted_verification:
        check(
            "verification",
            wanted_verification == actual_verification,
            "verification status differs",
        )

    response = observation.get("response") or {}
    text = str(response.get("text") or observation.get("text") or "")
    normalized_text = _normal(text)
    for fact in expected["response_facts"]:
        check(
            f"response.fact.{_normal(fact)}",
            _normal(fact) in normalized_text,
            f"required response fact is absent: {fact}",
        )
    for claim in expected["forbidden_claims"]:
        check(
            f"response.forbidden.{_normal(claim)}",
            _normal(claim) not in normalized_text,
            f"forbidden claim is present: {claim}",
        )

    style = expected["style"]
    if style.get("max_words") is not None:
        check(
            "style.max_words",
            len(text.split()) <= int(style["max_words"]),
            "response exceeds the word budget",
        )
    forbidden_phrases = list(style.get("forbidden_phrases") or ())
    check(
        "style.forbidden_phrases",
        not any(_normal(phrase) in normalized_text for phrase in forbidden_phrases),
        "response contains a disallowed style phrase",
    )
    check(
        "security.secrets",
        not _SECRET.search(text),
        "response contains a secret-like value",
    )

    tags = tuple(str(item) for item in case.get("tags", ()))
    return GradeResult(
        str(case["id"]),
        tags[0] if tags else "uncategorized",
        str(case.get("stage") or "response"),
        not failures,
        tuple(failures),
        checks,
    )


__all__ = ["GradeResult", "grade_case"]
