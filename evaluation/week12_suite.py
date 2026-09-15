"""Run the Week 1-2 deterministic understanding and smart-home regressions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.kernel.dialogue_state import DialogueStateStore
from agent.kernel.understanding import analyze_turn
from evaluation.home_assistant_simulator import HomeAssistantSimulator, SimulatedEntity
from evaluation.runner import evaluate_suite, summarize_results


def _routing_payload(analysis) -> dict[str, Any]:
    decisions = analysis.operational_decisions
    if len(decisions) == 1:
        return decisions[0].as_dict()
    if len(decisions) > 1:
        return {
            "intent": "compound_operation",
            "steps": [item.as_dict() for item in decisions],
        }
    return {"intent": "conversation", "risk": "none"}


def evaluate_week12_case(case: dict[str, Any]) -> dict[str, Any]:
    text = str(case["input"])
    history = list(case.get("history") or ())
    store = DialogueStateStore()
    resolution = store.resolve_references(
        text, platform="telegram", owner_id="evaluation", history=history
    )
    analysis = analyze_turn(
        text,
        resolution.resolved_text,
        owner_id="",
        platform="telegram",
        history=history,
        resolved_entities=resolution.entities,
    )
    response: dict[str, Any] = {
        "text": str(case.get("sample_response") or "Understood."),
        "routing": _routing_payload(analysis),
        "turn_state": analysis.state.as_dict(),
        "response_mode": analysis.state.response_mode.value,
        "tool_sequence": [
            item.selected_capability
            for item in analysis.operational_decisions
            if item.selected_capability
        ],
        "recent_responses": list(case.get("recent_responses") or ()),
    }
    final = (
        analysis.operational_decisions[-1] if analysis.operational_decisions else None
    )
    simulation = case.get("simulation")
    if final and final.selected_capability == "home_control" and simulation:
        entity = SimulatedEntity(
            "light.ai_sync_box_strip",
            "AI Sync Box strip",
            str(simulation.get("initial_state", "on")),
            ("dreamview", "dream view", "tv light"),
            bool(simulation.get("available", True)),
            True,
            str(simulation.get("transition", "immediate")),
        )
        simulator = HomeAssistantSimulator([entity])
        target = str(final.parameters.get("target") or "")
        result = simulator.control(
            target,
            str(final.parameters["state"]),
            verify_ticks=int(simulation.get("verify_ticks", 1)),
        )
        simulated = result.as_response()
        response["simulator_sequence"] = simulated.pop("tool_sequence")
        response.update(simulated)
    elif final and final.intent in {"clarification", "multiple_intents"}:
        response["text"] = str(final.parameters.get("message") or "Please clarify.")
    return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path(__file__).with_name("week12_scenarios.json"),
    )
    args = parser.parse_args()
    cases = json.loads(args.scenarios.read_text(encoding="utf-8"))
    responses = {str(case["id"]): evaluate_week12_case(case) for case in cases}
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
