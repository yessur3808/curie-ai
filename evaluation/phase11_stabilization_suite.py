"""Deterministic Phase 11 rollout, rollback, and scorecard evidence."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from agent.kernel.feature_flags import (
    PIPELINE_CONTROL_TOKEN,
    PipelineFeatureFlags,
    PipelineMode,
)
from agent.kernel.rollout import (
    LegacyRetention,
    RollbackTrigger,
    RolloutStage,
    clear_pipeline_rollback,
    evaluate_rollback_signals,
    set_rollout_stage,
    trigger_pipeline_rollback,
)
from agent.orchestration.turn_pipeline import LegacyTurnPipelineAdapter
from evaluation.scorecard import build_scorecard


def _event(text: str = "Hello", *, connector: str = "telegram") -> dict[str, str]:
    return {
        "text": text,
        "platform": connector,
        "internal_id": "owner-a",
    }


def _flags(path: Path, stage: RolloutStage) -> PipelineFeatureFlags:
    return PipelineFeatureFlags(
        rollout_stage=stage,
        rollout_state_file=path,
        active_owners=frozenset({"owner-a"}),
        active_connectors=frozenset({"telegram"}),
        smart_home_targets=frozenset({"floor lamp"}),
    )


def _rollout_matrix() -> dict[str, bool]:
    with tempfile.TemporaryDirectory(prefix="curie-phase11-") as directory:
        path = Path(directory) / "rollout.json"
        offline = _flags(path, RolloutStage.OFFLINE)
        shadow = _flags(path, RolloutStage.SHADOW)
        conversation = _flags(path, RolloutStage.CONVERSATION_CANARY)
        smart_home = _flags(path, RolloutStage.SMART_HOME_CANARY)
        expansion = _flags(path, RolloutStage.CONNECTOR_EXPANSION)
        default = _flags(path, RolloutStage.DEFAULT_ACTIVE)
        removed = _flags(path, RolloutStage.LEGACY_REMOVED)
        selected = smart_home.decision_for(_event("Turn Floor Lamp off"))
        return {
            "offline": offline.mode_for(_event()) is PipelineMode.LEGACY,
            "shadow": shadow.mode_for(_event()) is PipelineMode.SHADOW,
            "conversation_canary": (
                conversation.mode_for(_event()) is PipelineMode.ACTIVE
                and conversation.mode_for(_event("Turn Floor Lamp off"))
                is PipelineMode.LEGACY
            ),
            "smart_home_canary": (
                selected.mode is PipelineMode.ACTIVE
                and selected.require_verification
                and smart_home.mode_for(_event("Turn Kitchen Lamp off"))
                is PipelineMode.LEGACY
            ),
            "connector_expansion": (
                expansion.mode_for(_event()) is PipelineMode.ACTIVE
                and expansion.mode_for(_event(connector="api")) is PipelineMode.SHADOW
            ),
            "default_active": default.mode_for(_event()) is PipelineMode.ACTIVE,
            "legacy_removed": (
                removed.mode_for(
                    {
                        **_event(),
                        "_pipeline_mode": "legacy",
                        "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
                    }
                )
                is PipelineMode.ACTIVE
            ),
        }


def _rollback_matrix() -> dict[str, bool]:
    cases: Mapping[RollbackTrigger, Mapping[str, Any]] = {
        RollbackTrigger.WRONG_DEVICE_MUTATION: {"wrong_device_mutation": True},
        RollbackTrigger.FALSE_VERIFICATION_CLAIM: {"false_verification_claim": True},
        RollbackTrigger.CROSS_OWNER_DATA: {"cross_owner_data": True},
        RollbackTrigger.TOOL_ACTIVATION_REGRESSION: {
            "false_tool_activation_rate": 0.006
        },
        RollbackTrigger.DELIVERY_FAILURE_REGRESSION: {
            "delivery_failure_rate_increase": 0.051
        },
        RollbackTrigger.LATENCY_REGRESSION: {"p95_regression_windows": 2},
        RollbackTrigger.UNSUPPORTED_PROACTIVE_CLAIM: {
            "unsupported_proactive_claim": True
        },
        RollbackTrigger.INSTABILITY_REGRESSION: {"instability_rate_increase": 0.051},
        RollbackTrigger.SENSITIVE_DATA_LEAK: {"sensitive_data_leak": True},
    }
    return {
        trigger.value: evaluate_rollback_signals(signals) is trigger
        for trigger, signals in cases.items()
    }


def _operational_rollback() -> bool:
    with tempfile.TemporaryDirectory(prefix="curie-rollback-") as directory:
        path = Path(directory) / "rollout.json"
        flags = _flags(path, RolloutStage.OFFLINE)
        set_rollout_stage(RolloutStage.DEFAULT_ACTIVE, path)
        before = flags.mode_for(_event()) is PipelineMode.ACTIVE
        trigger_pipeline_rollback(
            RollbackTrigger.SENSITIVE_DATA_LEAK,
            evidence={"source": "phase11-evaluation"},
            path=path,
        )
        during = flags.mode_for(_event()) is PipelineMode.LEGACY
        clear_pipeline_rollback(path)
        after = flags.mode_for(_event()) is PipelineMode.ACTIVE
        return before and during and after


def run(
    metrics: Mapping[str, float],
    *,
    phase345: Mapping[str, Any],
    phase67: Mapping[str, Any],
    phase8: Mapping[str, Any],
    simulator: Mapping[str, Any],
    connector: Mapping[str, Any],
) -> dict[str, Any]:
    rollout = _rollout_matrix()
    rollbacks = _rollback_matrix()
    retention = LegacyRetention()
    retention_complete = bool(
        retention.owner
        and retention.removal_deadline
        and len(retention.rollback_revision) >= 7
    )
    scorecard = build_scorecard(
        metrics,
        phase345=phase345,
        phase67=phase67,
        phase8=phase8,
        simulator=simulator,
        connector=connector,
    )
    phase_metrics = {
        "rollout_stage_coverage": round(sum(rollout.values()) / len(rollout), 4),
        "rollback_trigger_coverage": round(sum(rollbacks.values()) / len(rollbacks), 4),
        "shadow_side_effect_safety": float(
            not LegacyTurnPipelineAdapter.SHADOW_ALLOWED_SIDE_EFFECTS
        ),
        "smart_home_canary_safety": float(rollout["smart_home_canary"]),
        "operational_rollback_tested": float(_operational_rollback()),
        "legacy_retention_completeness": float(retention_complete),
        "scorecard_measurement_coverage": scorecard["measurement_coverage"],
        "scorecard_pass_rate": scorecard["pass_rate"],
    }
    checks = {
        "all_rollout_stages": phase_metrics["rollout_stage_coverage"] == 1.0,
        "all_rollback_triggers": phase_metrics["rollback_trigger_coverage"] == 1.0,
        "shadow_has_no_side_effects": phase_metrics["shadow_side_effect_safety"] == 1.0,
        "device_canary_requires_verification": phase_metrics["smart_home_canary_safety"]
        == 1.0,
        "rollback_was_exercised": phase_metrics["operational_rollback_tested"] == 1.0,
        "legacy_has_owner_and_deadline": phase_metrics["legacy_retention_completeness"]
        == 1.0,
        "scorecard_is_complete": phase_metrics["scorecard_measurement_coverage"] == 1.0,
        "scorecard_reaches_nine_of_ten": phase_metrics["scorecard_pass_rate"] >= 0.9,
    }
    return {
        "schema_version": 1,
        "metrics": phase_metrics,
        "checks": checks,
        "rollout_stages": rollout,
        "rollback_triggers": rollbacks,
        "legacy_retention": retention.as_dict(),
        "scorecard": scorecard,
    }


def main() -> None:
    from evaluation.phase9_suite import run as run_release_suite

    report = run_release_suite()["per_stage"]["rollout_stabilization"]
    print(json.dumps(report, indent=2, sort_keys=True))
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
