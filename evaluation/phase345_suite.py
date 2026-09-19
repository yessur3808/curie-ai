"""Deterministic release gate for Phases 3, 4, and 5."""

from __future__ import annotations

import json

from agent.intent_router import classify_request
from agent.kernel.planning import build_execution_plan
from agent.kernel.understanding import analyze_turn
from agent.routing import route_operational_request
from agent.understanding.decomposition import decompose_request
from agent.understanding.entities import DeviceResolver, ResolutionStatus
from agent.understanding.recognizers import recognize_deterministic
from agent.understanding.taxonomy import INTENT_TAXONOMY, IntentLeaf
from services.smart_home.models import CanonicalDevice, DeviceSnapshot


def _device(name: str, device_id: str, room: str) -> CanonicalDevice:
    snapshot = DeviceSnapshot(
        "sim",
        device_id,
        name,
        "light",
        True,
        "on",
        False,
        True,
        {},
        {"room": room},
    )
    return CanonicalDevice.from_snapshot(
        snapshot, normalized_name=" ".join(name.casefold().split())
    )


def _classification_metrics() -> dict[str, float | int]:
    positives: list[tuple[str, IntentLeaf]] = []
    positives.extend(
        (f"Turn lamp {index} off", IntentLeaf.DEVICE_STATE_MUTATION)
        for index in range(50)
    )
    positives.extend(
        (f"/approve action {index:08x}", IntentLeaf.APPROVAL) for index in range(50)
    )
    positives.extend(
        ("Emergency stop now" + "!" * index, IntentLeaf.EMERGENCY_STOP)
        for index in range(1, 51)
    )
    positives.extend(
        (
            f"There is no device called Imaginary Lamp {index}",
            IntentLeaf.MEMORY_CORRECT,
        )
        for index in range(50)
    )
    predictions = [
        (
            text,
            expected,
            result.intent if (result := recognize_deterministic(text)) else None,
        )
        for text, expected in positives
    ]
    labels = {expected for _, expected, _ in predictions}
    f1_values = []
    recalls = []
    for label in labels:
        tp = sum(
            expected is label and predicted is label
            for _, expected, predicted in predictions
        )
        fp = sum(
            expected is not label and predicted is label
            for _, expected, predicted in predictions
        )
        fn = sum(
            expected is label and predicted is not label
            for _, expected, predicted in predictions
        )
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1_values.append(2 * precision * recall / max(precision + recall, 1e-9))
        recalls.append(recall)

    ordinary = [
        f"In story {index}, the phrase 'turn the lamp off' is only a metaphor."
        for index in range(100)
    ] + [
        f"We discussed weather and deployment vocabulary in chapter {index}."
        for index in range(100)
    ]
    false_activations = sum(
        route_operational_request(text, "") is not None for text in ordinary
    )
    clear_requests = [f"Turn lamp {index} off" for index in range(100)]
    unnecessary_clarifications = sum(
        (decision := route_operational_request(text, "")) is not None
        and decision.intent == "clarification"
        for text in clear_requests
    )
    return {
        "positive_examples": len(positives),
        "near_negative_examples": len(ordinary),
        "macro_f1": round(sum(f1_values) / len(f1_values), 4),
        "minimum_critical_recall": round(min(recalls), 4),
        "false_tool_activation_rate": round(false_activations / len(ordinary), 4),
        "unnecessary_clarification_rate": round(
            unnecessary_clarifications / len(clear_requests), 4
        ),
    }


def _compound_metrics() -> dict[str, int]:
    compound = [
        f"Check RAM usage and then check hardware specs for sample {index}"
        for index in range(100)
    ]
    decomposed = 0
    for text in compound:
        result = decompose_request(
            text,
            independently_meaningful=lambda clause: bool(
                route_operational_request(clause, "")
            ),
        )
        decomposed += int(result.decomposed and result.preserve_order)
    corrections = [f"There is no device called Phantom {index}" for index in range(50)]
    correction_routes = sum(
        (request := classify_request(text)) is not None
        and request.action == "home_alias_reject"
        for text in corrections
    )
    followups = sum(
        recognize_deterministic("yes", pending_approval=True).intent
        is IntentLeaf.APPROVAL
        for _ in range(100)
    )
    return {
        "compound_examples": len(compound),
        "compound_passed": decomposed,
        "multi_turn_followups": 100,
        "multi_turn_passed": followups,
        "correction_exchanges": len(corrections),
        "correction_passed": correction_routes,
    }


def _device_metrics() -> dict[str, float | int]:
    resolver = DeviceResolver()
    devices = [
        _device("Kitchen Lamp", "kitchen", "kitchen"),
        _device("Floor Lamp", "floor", "living room"),
    ]
    groups = (
        ["all lights"] * 25
        + ["every lamp"] * 25
        + ["online lights"] * 25
        + ["kitchen lights"] * 25
    )
    group_passed = sum(
        resolver.resolve(target, devices).status is ResolutionStatus.GROUP
        for target in groups
    )
    fuzzy_queries = [
        ("kithen lamp", "sim:kitchen"),
        ("flor lamp", "sim:floor"),
    ] * 50
    wrong = 0
    abstained = 0
    for query, expected in fuzzy_queries:
        result = resolver.resolve(query, devices)
        if not result.resolved:
            abstained += 1
        elif result.devices[0].canonical_id != expected:
            wrong += 1
    return {
        "group_examples": len(groups),
        "group_accuracy": round(group_passed / len(groups), 4),
        "fuzzy_examples": len(fuzzy_queries),
        "wrong_fuzzy_mutations": wrong,
        "safe_fuzzy_abstentions": abstained,
    }


def _planning_metrics() -> dict[str, int]:
    stable = 0
    for index in range(100):
        request_key = f"telegram:account:chat:{index}"
        first = analyze_turn(
            "Turn Floor Lamp off",
            "Turn Floor Lamp off",
            owner_id="owner",
            platform="telegram",
            request_key=request_key,
        )
        second = analyze_turn(
            "Turn Floor Lamp off",
            "Turn Floor Lamp off",
            owner_id="owner",
            platform="telegram",
            request_key=request_key,
        )
        one = build_execution_plan(first.state, first.operational_decisions)
        two = build_execution_plan(second.state, second.operational_decisions)
        stable += int(
            one.plan_hash == two.plan_hash
            and one.steps[0].idempotency_key == two.steps[0].idempotency_key
        )
    return {
        "plan_idempotency_examples": 100,
        "stable_plan_bindings": stable,
    }


def run() -> dict:
    taxonomy_coverage = {
        leaf.value: len(definition.evaluation_examples())
        for leaf, definition in INTENT_TAXONOMY.items()
    }
    report = {
        "taxonomy_version": "3.0.0",
        "taxonomy_leaves": len(INTENT_TAXONOMY),
        "minimum_examples_per_leaf": min(taxonomy_coverage.values()),
        **_classification_metrics(),
        **_compound_metrics(),
        **_device_metrics(),
        **_planning_metrics(),
    }
    checks = {
        "macro_f1": report["macro_f1"] >= 0.95,
        "critical_recall": report["minimum_critical_recall"] >= 0.97,
        "false_activation": report["false_tool_activation_rate"] < 0.005,
        "clarification": report["unnecessary_clarification_rate"] < 0.03,
        "taxonomy_coverage": report["minimum_examples_per_leaf"] >= 20,
        "compound": report["compound_passed"] == report["compound_examples"],
        "followups": report["multi_turn_passed"] == report["multi_turn_followups"],
        "corrections": report["correction_passed"] == report["correction_exchanges"],
        "groups": report["group_accuracy"] >= 0.99,
        "fuzzy_safety": report["wrong_fuzzy_mutations"] == 0,
        "plan_bindings": report["stable_plan_bindings"]
        == report["plan_idempotency_examples"],
    }
    report["checks"] = checks
    report["score"] = round(10 * sum(checks.values()) / len(checks), 2)
    return report


def main() -> None:
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
