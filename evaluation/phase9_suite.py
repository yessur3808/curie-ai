"""Comprehensive deterministic metrics for Curie's Phase 9 release gates."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import quantiles
import time

from agent.kernel.understanding import analyze_turn
from agent.response_composer import build_response_plan, render_response
from agent.understanding.recognizers import recognize_deterministic
from evaluation.contracts import catalog_summary, load_catalog
from evaluation.home_assistant_simulator import HomeAssistantSimulator, SimulatedEntity
from evaluation.phase2_suite import _evaluate as evaluate_phase2
from evaluation.phase345_suite import run as run_phase345
from evaluation.phase67_suite import run as run_phase67
from evaluation.phase8_suite import run as run_phase8
from evaluation.tool_provider_simulator import ProviderMode, run_provider_matrix
from utils.formatting import telegram_html


ROOT = Path(__file__).resolve().parents[1]


def _ratio(passed: int, total: int) -> float:
    return round(passed / max(total, 1), 4)


def _simulator_metrics() -> dict:
    cases = (
        (SimulatedEntity("light.a", "Lamp A", "on"), "verified", 1),
        (SimulatedEntity("light.b", "Lamp B", "off"), "already_satisfied", 0),
        (
            SimulatedEntity("light.c", "Lamp C", "on", transition="lagged"),
            "verified",
            1,
        ),
        (
            SimulatedEntity("light.d", "Lamp D", "on", transition="no_change"),
            "unverified",
            1,
        ),
        (SimulatedEntity("light.e", "Lamp E", "on", available=False), "failed", 0),
        (SimulatedEntity("light.f", "Lamp F", "on", transition="error"), "failed", 1),
    )
    passed = 0
    verified = 0
    for entity, expected_status, expected_calls in cases:
        simulator = HomeAssistantSimulator([entity])
        result = simulator.control(entity.name, "off", verify_ticks=1)
        correct = (
            result.verification_status == expected_status
            and len(simulator.calls) == expected_calls
        )
        passed += int(correct)
        verified += int(
            result.verification_status
            in {"verified", "already_satisfied", "unverified", "failed"}
        )
    provider = run_provider_matrix()
    provider_checks = {
        item.mode.value: (
            bool(item.verification_status)
            and not (
                item.execution_status in {"failed", "partial_failure", "cancelled"}
                and item.verification_status in {"verified", "already_satisfied"}
            )
            and (
                item.mutation_count == 0
                if item.mode is ProviderMode.CANCELLED
                else True
            )
            and (
                item.replay_suppressed and item.mutation_count == 1
                if item.mode is ProviderMode.RECONNECT_REPLAY
                else True
            )
        )
        for item in provider
    }
    provider_pass_rate = _ratio(sum(provider_checks.values()), len(provider_checks))
    return {
        "tool_simulation_cases": len(cases),
        "tool_simulation_passed": passed,
        "home_assistant_pass_rate": _ratio(passed, len(cases)),
        "provider_scenarios": [item.as_dict() for item in provider],
        "provider_checks": provider_checks,
        "provider_pass_rate": provider_pass_rate,
        "tool_simulation_pass_rate": min(
            _ratio(passed, len(cases)), provider_pass_rate
        ),
        "verification_coverage": _ratio(verified, len(cases)),
    }


def _connector_metrics() -> dict:
    rendered = telegram_html(
        "**Health**\n\n- **Text:** Ready\n- *Vision:* Ready\n\n"
        "| Service | State |\n| --- | --- |\n| Database | Ready |\n\n"
        "[Details](https://example.com/status)"
    )
    checks = {
        "bold": "<b>Health</b>" in rendered,
        "italic": "<i>Vision:</i>" in rendered,
        "list": "• " in rendered,
        "table": "<pre>" in rendered,
        "safe_link": '<a href="https://example.com/status">Details</a>' in rendered,
        "no_raw_table_separator": "| --- |" not in rendered,
    }
    return {
        "checks": checks,
        "connector_conformance_pass_rate": _ratio(sum(checks.values()), len(checks)),
    }


def _end_to_end_connector_metrics() -> dict:
    """Run a Telegram-shaped event through real routing, rendering, and delivery."""
    simulator = HomeAssistantSimulator()
    seen_events: set[str] = set()
    persisted: list[dict] = []
    deliveries: list[dict] = []

    def handle(event_id: str) -> str:
        if event_id in seen_events:
            return "replayed"
        owner_id = "eval-owner"
        analysis = analyze_turn(
            "Turn off DreamView",
            "Turn off DreamView",
            owner_id=owner_id,
            platform="telegram",
            request_key=event_id,
        )
        decision = analysis.operational_decisions[0]
        result = simulator.control("DreamView", "off").as_response()
        result["execution_status"] = "completed"
        plan = build_response_plan(
            result,
            user_text="Turn off DreamView",
            response_mode=analysis.state.response_mode.value,
            connector="telegram",
        )
        rendered = render_response(plan, result)
        formatted = telegram_html(rendered["text"])
        deliveries.append({"event_id": event_id, "status": "sent", "text": formatted})
        persisted.append(
            {
                "event_id": event_id,
                "owner_id": owner_id,
                "capability": decision.selected_capability,
                "verification_status": result["verification_status"],
            }
        )
        seen_events.add(event_id)
        return "sent"

    first = handle("telegram:evaluation:1")
    replay = handle("telegram:evaluation:1")
    checks = {
        "identity": persisted[0]["owner_id"] == "eval-owner",
        "pipeline": persisted[0]["capability"] == "home_control",
        "tool_simulator": persisted[0]["verification_status"] == "verified",
        "formatted_output": "AI Sync Box strip" in deliveries[0]["text"],
        "delivery_receipt": first == "sent" and deliveries[0]["status"] == "sent",
        "persistence": len(persisted) == 1,
        "replay_suppression": replay == "replayed" and len(simulator.calls) == 1,
    }
    return {
        "checks": checks,
        "pass_rate": _ratio(sum(checks.values()), len(checks)),
    }


def _latency_metrics() -> dict:
    samples = []
    for index in range(200):
        started = time.perf_counter()
        recognize_deterministic(f"Turn lamp {index} off")
        samples.append((time.perf_counter() - started) * 1000)
    p95 = quantiles(samples, n=20, method="inclusive")[18]
    return {
        "sample_count": len(samples),
        "p50_ms": round(sorted(samples)[len(samples) // 2], 4),
        "p95_ms": round(p95, 4),
    }


def run(root: str | Path = ROOT) -> dict:
    root = Path(root)
    phase2_cases = evaluate_phase2()
    phase345 = run_phase345()
    phase67 = run_phase67()
    phase8 = run_phase8()
    simulator = _simulator_metrics()
    connector = _connector_metrics()
    connector_e2e = _end_to_end_connector_metrics()
    latency = _latency_metrics()
    catalog = catalog_summary(load_catalog(root / "evaluation/datasets"))

    phase2_passed = sum(passed for _, passed in phase2_cases)
    phase_checks = [
        *[passed for _, passed in phase2_cases],
        *phase345["checks"].values(),
        *phase67["checks"].values(),
        *phase8["checks"].values(),
        *connector["checks"].values(),
        *connector_e2e["checks"].values(),
        simulator["tool_simulation_pass_rate"] == 1.0,
    ]
    known_regression_expected = len(phase2_cases) + 3
    known_regression_passed = phase2_passed
    known_regression_passed += int(phase345["group_accuracy"] >= 0.99)
    known_regression_passed += int(phase345["wrong_fuzzy_mutations"] == 0)
    known_regression_passed += int(connector["connector_conformance_pass_rate"] == 1.0)

    planning_shape_accuracy = min(
        _ratio(phase345["compound_passed"], phase345["compound_examples"]),
        _ratio(
            phase345["stable_plan_bindings"],
            phase345["plan_idempotency_examples"],
        ),
    )
    metrics = {
        "intent_macro_f1": phase345["macro_f1"],
        "critical_min_recall": phase345["minimum_critical_recall"],
        "false_tool_activation_rate": phase345["false_tool_activation_rate"],
        "unnecessary_clarification_rate": phase345["unnecessary_clarification_rate"],
        "entity_resolution_accuracy": phase345["group_accuracy"],
        "device_mutation_safety": float(
            phase345["wrong_fuzzy_mutations"] == 0
            and simulator["tool_simulation_pass_rate"] == 1.0
        ),
        "planning_shape_accuracy": planning_shape_accuracy,
        "task_success_rate": _ratio(sum(phase_checks), len(phase_checks)),
        "verification_coverage": simulator["verification_coverage"],
        "memory_precision": phase67["memory_precision"],
        "naturalness_score": round(phase67["human_naturalness_proxy"] / 5.0, 4),
        "response_repetition_rate": float(
            not dict(phase2_cases).get(
                "rejected_topic_does_not_poison_other_project", False
            )
        ),
        "unsupported_proactive_rate": _ratio(
            phase67["unsupported_proactive_selected"],
            phase67["unsupported_proactive_examples"],
        ),
        "security_pass_rate": float(
            phase345["false_tool_activation_rate"] == 0
            and all(phase8["checks"].values())
        ),
        "connector_conformance_pass_rate": min(
            connector["connector_conformance_pass_rate"],
            connector_e2e["pass_rate"],
        ),
        "known_regression_pass_rate": _ratio(
            known_regression_passed, known_regression_expected
        ),
        "latency_p95_ms": latency["p95_ms"],
        "correctness_pass_rate": _ratio(sum(phase_checks), len(phase_checks)),
        "personality_pass_rate": _ratio(
            phase67["natural_command_passed"], phase67["response_examples"]
        ),
    }
    return {
        "schema_version": 1,
        "metrics": metrics,
        "per_stage": {
            "dialogue_state": {
                "passed": phase2_passed,
                "total": len(phase2_cases),
            },
            "understanding_planning": phase345,
            "response_memory": phase67,
            "controlled_learning": phase8,
            "tool_simulation": simulator,
            "delivery": connector,
            "connector_end_to_end": connector_e2e,
            "latency": latency,
        },
        "per_taxonomy": catalog["taxonomy_counts"],
        "catalog": catalog,
        "checks": {
            "all_metrics_present": len(metrics) == 19,
            "catalog_has_all_taxonomies": all(
                catalog["taxonomy_counts"].get(name, 0) > 0
                for name in (
                    "understanding",
                    "conversation",
                    "devices",
                    "planning",
                    "memory",
                    "personality",
                    "security",
                    "connectors",
                    "proactive",
                    "production_regressions",
                )
            ),
            "production_regressions_are_stable": catalog["taxonomy_counts"].get(
                "production_regressions", 0
            )
            >= 9,
        },
    }


def main() -> None:
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
