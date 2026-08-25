"""Deterministic response-quality gate for Curie conversation transcripts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate captured Curie responses")
    parser.add_argument("responses", type=Path, help="JSON object keyed by scenario ID")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path(__file__).with_name("scenarios.json"),
    )
    args = parser.parse_args()
    cases = json.loads(args.scenarios.read_text())
    responses = json.loads(args.responses.read_text())
    results = evaluate_suite(cases, responses)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        detail = "" if result.passed else f": {', '.join(result.failures)}"
        print(f"{status} {result.case_id} [{result.category}]{detail}")
    passed = sum(result.passed for result in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
