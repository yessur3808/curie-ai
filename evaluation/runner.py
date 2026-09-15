"""Deterministic response-quality gate for Curie conversation transcripts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

_URL = re.compile(r"https?://[^\s)]+", re.I)
_THINKING = re.compile(r"<\/?think>|chain of thought|my reasoning process", re.I)
_OVERFORMAL = re.compile(
    r"\b(?:functioning optimally|systems are humming|at your service|"
    r"how may i assist|delighted to assist)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    case_id: str
    category: str
    passed: bool
    failures: tuple[str, ...]
    correctness_passed: bool = True
    personality_passed: bool = True


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    total: int
    passed: int
    overall_score: float
    correctness_score: float
    personality_score: float
    categories: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "overall_score": self.overall_score,
            "correctness_score": self.correctness_score,
            "personality_score": self.personality_score,
            "categories": dict(self.categories),
        }


def _routing_capabilities(response: dict) -> list[str]:
    routing = response.get("routing") or {}
    capabilities = []
    if routing.get("selected_capability"):
        capabilities.append(str(routing["selected_capability"]))
    for step in routing.get("steps", ()):
        if isinstance(step, dict) and step.get("selected_capability"):
            capabilities.append(str(step["selected_capability"]))
    state = response.get("turn_state") or {}
    for subgoal in (state.get("goal") or {}).get("subgoals", ()):
        if isinstance(subgoal, dict) and subgoal.get("capability"):
            capabilities.append(str(subgoal["capability"]))
    return list(dict.fromkeys(capabilities))


def _recent_similarity(text: str, recent: list[str]) -> float:
    current = set(re.findall(r"[a-z0-9]+", text.casefold()))
    if not current:
        return 0.0
    scores = []
    for item in recent:
        previous = set(re.findall(r"[a-z0-9]+", str(item).casefold()))
        scores.append(len(current & previous) / max(len(current | previous), 1))
    return max(scores, default=0.0)


def evaluate_case(case: dict, response: dict) -> EvaluationResult:
    text = str(response.get("text", "")).strip()
    expected = case.get("expected", {})
    failures = []
    correctness_failures = []
    personality_failures = []
    words = text.split()

    if len(words) < int(expected.get("min_words", 0)):
        personality_failures.append("response is too shallow")
    if len(words) > int(expected.get("max_words", 10**9)):
        personality_failures.append("response is too verbose")
    if any(
        token.casefold() not in text.casefold()
        for token in expected.get("contains", [])
    ):
        correctness_failures.append("required content is missing")
    if any(
        token.casefold() in text.casefold() for token in expected.get("forbidden", [])
    ):
        correctness_failures.append("forbidden content is present")
    if "exact" in expected and text != str(expected["exact"]):
        correctness_failures.append("exact answer is incorrect")
    if expected.get("provenance") and not response.get("provenance"):
        correctness_failures.append("typed provenance is missing")
    if expected.get("citation") and not _URL.search(text):
        correctness_failures.append("live-source citation is missing")
    if expected.get("approval"):
        approval_words = ("approve", "permission", "shall i", "would you like me")
        if not any(word in text.casefold() for word in approval_words):
            correctness_failures.append("approval request is missing")
    if expected.get("personality"):
        if "—" in text or ";" in text:
            personality_failures.append("disallowed punctuation is present")
        if _OVERFORMAL.search(text):
            personality_failures.append("response sounds overly formal or theatrical")
    if expected.get("hide_reasoning") and _THINKING.search(text):
        correctness_failures.append("hidden reasoning leaked into the response")
    max_latency = expected.get("max_latency_ms")
    latency = response.get("processing_time_ms")
    if max_latency is not None and (
        latency is None or float(latency) > float(max_latency)
    ):
        correctness_failures.append("latency exceeds the scenario budget")

    routing = response.get("routing") or {}
    turn_state = response.get("turn_state") or {}
    goal = turn_state.get("goal") or {}
    actual_intent = routing.get("intent") or goal.get("intent")
    if expected.get("route_intent") and actual_intent != expected["route_intent"]:
        correctness_failures.append("routing intent is incorrect")
    if expected.get("capability") and expected[
        "capability"
    ] not in _routing_capabilities(response):
        correctness_failures.append("selected capability is incorrect")
    if expected.get("tool_sequence"):
        sequence = response.get("tool_sequence") or _routing_capabilities(response)
        if list(sequence) != list(expected["tool_sequence"]):
            correctness_failures.append("tool sequence is incorrect")
    actual_risk = routing.get("risk") or goal.get("risk")
    if expected.get("risk") and actual_risk != expected["risk"]:
        correctness_failures.append("risk classification is incorrect")
    if (
        expected.get("response_mode")
        and (response.get("response_mode") or turn_state.get("response_mode"))
        != expected["response_mode"]
    ):
        correctness_failures.append("response mode is incorrect")
    if (
        expected.get("memory_policy")
        and turn_state.get("memory_policy") != expected["memory_policy"]
    ):
        correctness_failures.append("memory policy is incorrect")
    if expected.get("entities"):
        actual_entities = {
            str(item.get("resolved_name", "")).casefold()
            for item in turn_state.get("entities", ())
            if isinstance(item, dict)
        }
        if any(
            str(item).casefold() not in actual_entities for item in expected["entities"]
        ):
            correctness_failures.append("entity resolution is incorrect")
    if (
        expected.get("verification_status")
        and response.get("verification_status") != expected["verification_status"]
    ):
        correctness_failures.append("verification status is incorrect")
    execution = response.get("execution") or {}
    plan = execution.get("plan") or {}
    if (
        expected.get("execution_status")
        and (response.get("execution_status") or execution.get("status"))
        != expected["execution_status"]
    ):
        correctness_failures.append("execution status is incorrect")
    if (
        expected.get("execution_mode")
        and plan.get("execution_mode") != expected["execution_mode"]
    ):
        correctness_failures.append("execution mode is incorrect")
    if "message_parts" in expected and len(response.get("message_parts") or ()) != int(
        expected["message_parts"]
    ):
        correctness_failures.append("message-part count is incorrect")
    if "clarification" in expected:
        actual_clarification = actual_intent in {"clarification", "multiple_intents"}
        if actual_clarification is not bool(expected["clarification"]):
            correctness_failures.append("clarification behavior is incorrect")
    if "max_recent_similarity" in expected:
        similarity = _recent_similarity(
            text, list(response.get("recent_responses") or ())
        )
        if similarity > float(expected["max_recent_similarity"]):
            personality_failures.append("response repeats a recent answer")

    failures.extend(correctness_failures)
    failures.extend(personality_failures)

    return EvaluationResult(
        str(case["id"]),
        str(case["category"]),
        not failures,
        tuple(failures),
        not correctness_failures,
        not personality_failures,
    )


def evaluate_suite(
    cases: list[dict], responses: dict[str, dict]
) -> list[EvaluationResult]:
    return [evaluate_case(case, responses.get(str(case["id"]), {})) for case in cases]


def summarize_results(results: list[EvaluationResult]) -> EvaluationSummary:
    total = len(results)
    if not total:
        return EvaluationSummary(0, 0, 0.0, 0.0, 0.0, {})
    categories: dict[str, list[bool]] = {}
    for result in results:
        categories.setdefault(result.category, []).append(result.passed)
    return EvaluationSummary(
        total=total,
        passed=sum(item.passed for item in results),
        overall_score=round(sum(item.passed for item in results) / total * 10, 2),
        correctness_score=round(
            sum(item.correctness_passed for item in results) / total * 10, 2
        ),
        personality_score=round(
            sum(item.personality_passed for item in results) / total * 10, 2
        ),
        categories={
            category: round(sum(values) / len(values) * 10, 2)
            for category, values in sorted(categories.items())
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate captured Curie responses")
    parser.add_argument("responses", type=Path, help="JSON object keyed by scenario ID")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path(__file__).with_name("scenarios.json"),
    )
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    cases = json.loads(args.scenarios.read_text())
    responses = json.loads(args.responses.read_text())
    results = evaluate_suite(cases, responses)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        detail = "" if result.passed else f": {', '.join(result.failures)}"
        print(f"{status} {result.case_id} [{result.category}]{detail}")
    passed = sum(result.passed for result in results)
    summary = summarize_results(results)
    print(f"\n{passed}/{len(results)} scenarios passed ({summary.overall_score}/10)")
    if args.json_report:
        args.json_report.write_text(
            json.dumps(
                {
                    "summary": summary.as_dict(),
                    "results": [
                        {
                            "case_id": item.case_id,
                            "category": item.category,
                            "passed": item.passed,
                            "failures": list(item.failures),
                        }
                        for item in results
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
