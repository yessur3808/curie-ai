"""Run deterministic Week 3-4 planning and execution regressions."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agent.kernel.planning import PlanExecutor, build_execution_plan
from agent.kernel.understanding import analyze_turn
from agent.orchestration.contracts import ResponseCandidate
from evaluation.runner import evaluate_suite, summarize_results


def _routing_payload(analysis) -> dict[str, Any]:
    if len(analysis.operational_decisions) == 1:
        return analysis.operational_decisions[0].as_dict()
    return {
        "intent": "compound_operation",
        "risk": analysis.state.goal.risk,
        "steps": [item.as_dict() for item in analysis.operational_decisions],
    }


async def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    text = str(case["input"])
    analysis = analyze_turn(
        text,
        text,
        owner_id="evaluation",
        platform="telegram",
    )
    plan = build_execution_plan(
        analysis.state,
        analysis.operational_decisions,
        preserve_order=analysis.preserve_order,
    )

    async def execute(decision):
        capability = str(
            decision.selected_capability
            or decision.parameters.get("command")
            or decision.intent
        )
        if capability == case.get("fail_capability"):
            raise RuntimeError("simulated failure")
        status = "verified" if decision.risk == "mutating" else "completed"
        verification = "verified" if decision.risk == "mutating" else "not_required"
        return ResponseCandidate(
            f"{capability.replace('_', ' ').title()} complete.",
            f"simulated:{capability}",
            {"status": status, "verification_status": verification},
        )

    execution = await PlanExecutor().execute(plan, execute)
    return {
        "text": execution.text,
        "routing": _routing_payload(analysis),
        "turn_state": analysis.state.as_dict(),
        "response_mode": analysis.state.response_mode.value,
        "tool_sequence": [
            item.selected_capability
            for item in analysis.operational_decisions
            if item.selected_capability
        ],
        "execution": execution.as_dict(),
        "execution_status": execution.status,
        "verification_status": execution.verification_status,
        "message_parts": execution.message_parts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path(__file__).with_name("week34_scenarios.json"),
    )
    args = parser.parse_args()
    cases = json.loads(args.scenarios.read_text(encoding="utf-8"))
    responses = {str(case["id"]): asyncio.run(_evaluate_case(case)) for case in cases}
    results = evaluate_suite(cases, responses)
    summary = summarize_results(results)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        detail = "" if result.passed else ": " + ", ".join(result.failures)
        print(f"{status} {result.case_id} [{result.category}]{detail}")
    print(json.dumps(summary.as_dict(), indent=2))
    return 0 if summary.passed == summary.total else 1


if __name__ == "__main__":
    raise SystemExit(main())
