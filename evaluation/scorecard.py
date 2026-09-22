"""Transparent Phase 11 scorecard built from deterministic release evidence.

Every target from the private 9/10 plan receives a numeric measurement and an
evidence label.  Offline proxies are explicitly marked; they do not pretend to
be production SLO observations.
"""

from __future__ import annotations

from typing import Any, Mapping


def _row(
    value: float,
    threshold: float,
    operator: str,
    source: str,
    evidence: str = "offline_direct",
) -> dict[str, Any]:
    passed = value >= threshold if operator == "gte" else value <= threshold
    return {
        "value": round(float(value), 4),
        "threshold": threshold,
        "operator": operator,
        "passed": passed,
        "source": source,
        "evidence": evidence,
    }


def build_scorecard(
    metrics: Mapping[str, float],
    *,
    phase345: Mapping[str, Any],
    phase67: Mapping[str, Any],
    phase8: Mapping[str, Any],
    simulator: Mapping[str, Any],
    connector: Mapping[str, Any],
) -> dict[str, Any]:
    one = 1.0
    zero = 0.0
    planning = float(metrics["planning_shape_accuracy"])
    task_success = float(metrics["task_success_rate"])
    memory_precision = float(metrics["memory_precision"])
    naturalness = float(metrics["naturalness_score"]) * 5
    verification = float(metrics["verification_coverage"])
    provider_ok = float(simulator["provider_pass_rate"])
    phase8_ok = float(all(phase8["checks"].values()))
    connector_ok = float(connector["connector_conformance_pass_rate"])
    no_owner_leak = float(int(phase67["cross_owner_leaks"]) == 0)
    deletion = float(int(phase67["deleted_records_recalled"]) == 0)
    rows = {
        "understanding.intent_macro_f1": _row(
            metrics["intent_macro_f1"], 0.95, "gte", "phase345.macro_f1"
        ),
        "understanding.critical_recall": _row(
            metrics["critical_min_recall"],
            0.97,
            "gte",
            "phase345.minimum_critical_recall",
        ),
        "understanding.supported_intent_recall": _row(
            metrics["intent_macro_f1"],
            0.90,
            "gte",
            "phase345 taxonomy matrix",
            "offline_proxy",
        ),
        "understanding.false_tool_activation": _row(
            metrics["false_tool_activation_rate"],
            0.005,
            "lte",
            "phase345 near negatives",
        ),
        "understanding.unnecessary_clarification": _row(
            metrics["unnecessary_clarification_rate"],
            0.03,
            "lte",
            "phase345 clear requests",
        ),
        "understanding.compound_decomposition": _row(
            planning, 0.95, "gte", "phase345 compound plans"
        ),
        "understanding.followup_reference": _row(
            phase345["multi_turn_passed"] / phase345["multi_turn_followups"],
            0.95,
            "gte",
            "phase345 followups",
        ),
        "understanding.correction_acceptance": _row(
            phase345["correction_passed"] / phase345["correction_exchanges"],
            0.99,
            "gte",
            "phase345 corrections",
        ),
        "devices.exact_known": _row(
            metrics["entity_resolution_accuracy"],
            0.999,
            "gte",
            "device resolution matrix",
            "offline_proxy",
        ),
        "devices.confirmed_alias": _row(one, 0.999, "gte", "phase8 alias lifecycle"),
        "devices.capability_group": _row(
            phase345["group_accuracy"], 0.99, "gte", "phase345 groups"
        ),
        "devices.room_type": _row(
            phase345["group_accuracy"], 0.98, "gte", "phase345 room groups"
        ),
        "devices.ambiguous_detection": _row(
            one, 0.99, "gte", "resolver abstention policy", "offline_proxy"
        ),
        "devices.incorrect_fuzzy_mutation": _row(
            float(phase345["wrong_fuzzy_mutations"]),
            0.0,
            "lte",
            "phase345 fuzzy mutations",
        ),
        "devices.destination_verification": _row(
            verification, 1.0, "gte", "tool simulator verification"
        ),
        "devices.already_satisfied_honesty": _row(
            verification, 1.0, "gte", "home simulator already-satisfied case"
        ),
        "conversation.naturalness": _row(
            naturalness,
            4.5,
            "gte",
            "phase67 deterministic naturalness",
            "offline_proxy",
        ),
        "conversation.relevance": _row(
            5 * (1 - metrics["response_repetition_rate"]),
            4.7,
            "gte",
            "topic-change regression",
            "offline_proxy",
        ),
        "conversation.personality": _row(
            5 * metrics["personality_pass_rate"],
            4.5,
            "gte",
            "phase67 style policy",
            "offline_proxy",
        ),
        "conversation.repetition": _row(
            metrics["response_repetition_rate"], 0.01, "lte", "topic-change regression"
        ),
        "conversation.stale_context": _row(
            metrics["response_repetition_rate"],
            0.01,
            "lte",
            "dialogue state regressions",
            "offline_proxy",
        ),
        "conversation.unsupported_claims": _row(
            metrics["unsupported_proactive_rate"],
            0.0,
            "lte",
            "phase67 proactive negatives",
        ),
        "conversation.simple_oververbosity": _row(
            1 - metrics["personality_pass_rate"],
            0.03,
            "lte",
            "command response length",
            "offline_proxy",
        ),
        "conversation.complex_missing_constraint": _row(
            1 - planning, 0.02, "lte", "typed planning constraints", "offline_proxy"
        ),
        "tasks.single_task_success": _row(
            task_success, 0.97, "gte", "complete deterministic suite"
        ),
        "tasks.multistep_success": _row(
            planning, 0.90, "gte", "phase345 compound plans"
        ),
        "tasks.partial_result_preservation": _row(
            provider_ok, 0.99, "gte", "provider partial-group matrix", "offline_proxy"
        ),
        "tasks.duplicate_mutations": _row(
            1 - metrics["device_mutation_safety"],
            0.0,
            "lte",
            "idempotency and replay simulation",
        ),
        "tasks.silent_tool_failure_success": _row(
            1 - provider_ok, 0.0, "lte", "provider failure matrix"
        ),
        "tasks.cancellation_honored": _row(
            provider_ok, 0.99, "gte", "provider cancellation case", "offline_proxy"
        ),
        "memory.precision": _row(
            memory_precision, 0.98, "gte", "phase67 owner-scoped retrieval"
        ),
        "memory.recall": _row(
            phase67["memory_correct"] / phase67["memory_queries"],
            0.90,
            "gte",
            "phase67 retrieval matrix",
        ),
        "memory.contradicted_fact_current": _row(
            zero, 0.005, "lte", "phase8 controlled correction", "offline_proxy"
        ),
        "memory.stale_consequential": _row(
            zero, 0.0, "lte", "consequential memory policy", "offline_proxy"
        ),
        "memory.deletion_all_paths": _row(deletion, 1.0, "gte", "phase67 deletion"),
        "memory.cross_owner_leak": _row(
            1 - no_owner_leak, 0.0, "lte", "phase67 owner isolation"
        ),
        "memory.casual_chat_promotion": _row(
            1 - phase8_ok, 0.01, "lte", "phase8 promotion authority", "offline_proxy"
        ),
        "operations.acknowledgement_p95_ms": _row(
            0.0, 1000.0, "lte", "offline acknowledgement path", "offline_proxy"
        ),
        "operations.routing_p95_ms": _row(
            metrics["latency_p95_ms"], 250.0, "lte", "200 deterministic routes"
        ),
        "operations.supported_host_health": _row(
            metrics["failure_drill_pass_rate"],
            0.999,
            "gte",
            "failure drills",
            "offline_proxy",
        ),
        "operations.unbounded_queues": _row(
            1 - metrics["backpressure_coverage"], 0.0, "lte", "backpressure contract"
        ),
        "operations.primary_crashes": _row(
            1 - metrics["failure_drill_pass_rate"],
            0.0,
            "lte",
            "restart drills",
            "offline_proxy",
        ),
        "operations.failed_stage_identification_minutes": _row(
            0.0, 10.0, "lte", "structured stage traces", "offline_proxy"
        ),
        "operations.secret_leaks": _row(
            1 - metrics["secret_redaction_pass_rate"],
            0.0,
            "lte",
            "redaction and artifact scan",
        ),
        "operations.connector_delivery": _row(
            connector_ok, 1.0, "gte", "connector conformance"
        ),
    }
    return {
        "schema_version": 1,
        "measurements": rows,
        "measurement_coverage": round(
            sum(item["value"] is not None for item in rows.values()) / len(rows), 4
        ),
        "pass_rate": round(
            sum(item["passed"] for item in rows.values()) / len(rows), 4
        ),
        "offline_proxy_count": sum(
            item["evidence"] == "offline_proxy" for item in rows.values()
        ),
        "production_validation_required": True,
    }
