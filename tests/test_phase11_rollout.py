import json

from agent.kernel.feature_flags import (
    PIPELINE_CONTROL_TOKEN,
    PipelineFeatureFlags,
    PipelineMode,
)
from agent.kernel.rollout import (
    RollbackTrigger,
    RolloutStage,
    clear_pipeline_rollback,
    detect_result_rollback,
    evaluate_rollback_signals,
    read_rollout_state,
    set_rollout_stage,
    trigger_pipeline_rollback,
)


def _flags(tmp_path, stage, **overrides):
    values = {
        "rollout_stage": stage,
        "rollout_state_file": tmp_path / "rollout.json",
        "active_owners": frozenset({"owner-a"}),
        "active_connectors": frozenset({"telegram"}),
        "smart_home_targets": frozenset({"floor lamp"}),
    }
    values.update(overrides)
    return PipelineFeatureFlags(**values)


def _event(text="Hello", owner="owner-a", platform="telegram"):
    return {"text": text, "internal_id": owner, "platform": platform}


def test_all_seven_rollout_stages_have_fail_closed_selection(tmp_path):
    assert (
        _flags(tmp_path, RolloutStage.OFFLINE).mode_for(_event()) is PipelineMode.LEGACY
    )
    assert (
        _flags(tmp_path, RolloutStage.SHADOW).mode_for(_event()) is PipelineMode.SHADOW
    )

    conversation = _flags(tmp_path, RolloutStage.CONVERSATION_CANARY)
    assert conversation.mode_for(_event()) is PipelineMode.ACTIVE
    assert conversation.mode_for(_event("Turn off Floor Lamp")) is PipelineMode.LEGACY
    assert conversation.mode_for(_event(owner="owner-b")) is PipelineMode.LEGACY

    smart_home = _flags(tmp_path, RolloutStage.SMART_HOME_CANARY)
    decision = smart_home.decision_for(_event("Turn off Floor Lamp"))
    assert decision.mode is PipelineMode.ACTIVE
    assert decision.require_verification is True
    assert smart_home.mode_for(_event("Turn off Kitchen Lamp")) is PipelineMode.LEGACY

    expansion = _flags(tmp_path, RolloutStage.CONNECTOR_EXPANSION)
    assert expansion.mode_for(_event()) is PipelineMode.ACTIVE
    assert expansion.mode_for(_event(platform="api")) is PipelineMode.SHADOW

    default = _flags(tmp_path, RolloutStage.DEFAULT_ACTIVE)
    assert (
        default.mode_for(_event(owner="owner-b", platform="api")) is PipelineMode.ACTIVE
    )

    removed = _flags(tmp_path, RolloutStage.LEGACY_REMOVED)
    assert (
        removed.mode_for(
            {
                **_event(),
                "_pipeline_mode": "legacy",
                "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
            }
        )
        is PipelineMode.ACTIVE
    )


def test_runtime_stage_change_and_rollback_need_no_redeploy(tmp_path):
    path = tmp_path / "rollout.json"
    flags = _flags(tmp_path, RolloutStage.OFFLINE)
    assert flags.mode_for(_event()) is PipelineMode.LEGACY

    set_rollout_stage(RolloutStage.DEFAULT_ACTIVE, path)
    assert flags.mode_for(_event()) is PipelineMode.ACTIVE
    trigger_pipeline_rollback(
        RollbackTrigger.WRONG_DEVICE_MUTATION,
        evidence={"count": 1},
        path=path,
    )
    assert flags.mode_for(_event()) is PipelineMode.LEGACY
    assert read_rollout_state(path)["rollback"]["trigger"] == "wrong_device_mutation"

    clear_pipeline_rollback(path)
    assert flags.mode_for(_event()) is PipelineMode.ACTIVE


def test_invalid_runtime_state_fails_closed(tmp_path):
    path = tmp_path / "rollout.json"
    path.write_text("not-json", encoding="utf-8")
    flags = _flags(tmp_path, RolloutStage.DEFAULT_ACTIVE)
    assert flags.mode_for(_event()) is PipelineMode.LEGACY
    assert read_rollout_state(path)["rollback"]["active"] is True


def test_all_nine_rollback_triggers_are_machine_detectable():
    cases = {
        "wrong_device_mutation": RollbackTrigger.WRONG_DEVICE_MUTATION,
        "false_verification_claim": RollbackTrigger.FALSE_VERIFICATION_CLAIM,
        "cross_owner_data": RollbackTrigger.CROSS_OWNER_DATA,
        "unsupported_proactive_claim": RollbackTrigger.UNSUPPORTED_PROACTIVE_CLAIM,
        "sensitive_data_leak": RollbackTrigger.SENSITIVE_DATA_LEAK,
        "false_tool_activation_rate": RollbackTrigger.TOOL_ACTIVATION_REGRESSION,
        "delivery_failure_rate_increase": RollbackTrigger.DELIVERY_FAILURE_REGRESSION,
        "p95_regression_windows": RollbackTrigger.LATENCY_REGRESSION,
        "instability_rate_increase": RollbackTrigger.INSTABILITY_REGRESSION,
    }
    values = {
        "false_tool_activation_rate": 0.006,
        "delivery_failure_rate_increase": 0.051,
        "p95_regression_windows": 2,
        "instability_rate_increase": 0.051,
    }
    for field, expected in cases.items():
        assert evaluate_rollback_signals({field: values.get(field, True)}) is expected


def test_result_guard_detects_false_verification_cross_owner_and_secrets():
    assert (
        detect_result_rollback(
            {"reported_verified": True, "verification_status": "unverified"}
        )
        is RollbackTrigger.FALSE_VERIFICATION_CLAIM
    )
    assert (
        detect_result_rollback({"owner_id": "owner-b"}, expected_owner="owner-a")
        is RollbackTrigger.CROSS_OWNER_DATA
    )
    credential_shape = "Bearer " + "z" * 28
    assert (
        detect_result_rollback({"text": credential_shape})
        is RollbackTrigger.SENSITIVE_DATA_LEAK
    )
    assert detect_result_rollback({"text": "All good."}) is None


def test_rollout_state_contains_no_raw_sensitive_evidence(tmp_path):
    path = tmp_path / "rollout.json"
    credential_shape = "Bearer " + "q" * 28
    trigger_pipeline_rollback(
        RollbackTrigger.SENSITIVE_DATA_LEAK,
        evidence={"authorization": credential_shape},
        path=path,
    )
    serialized = json.dumps(read_rollout_state(path), sort_keys=True)
    assert credential_shape not in serialized
    assert "[REDACTED]" in serialized
