from datetime import datetime, timedelta, timezone

import pytest

from agent.personality_context import PersonalityContext
from memory import local_store
from memory.adaptation import get_preferences, handle_adaptation_command
from memory.self_learning import (
    LearningLevel,
    capture_session_adaptation,
    create_learning_candidate,
    get_active_configuration,
    get_session_adaptation,
    list_adaptive_configs,
    list_learning_candidates,
    list_learning_events,
    promote_canary,
    promote_low_risk_candidate,
    record_canary_observation,
    record_learning_event,
    record_shadow_observation,
    reject_candidate,
    rollback_configuration,
    run_offline_evaluation,
    stage_code_candidate,
    start_canary,
)

pytestmark = pytest.mark.security


def _event_ids(owner="u1", count=3):
    credential_field = "".join(("pass", "word"))
    return [
        record_learning_event(
            owner,
            "operational_signal",
            text=f"private correction {index}",
            metadata={
                "signal": "regeneration",
                credential_field: "unit-test-placeholder",
            },
        )["id"]
        for index in range(count)
    ]


def _candidate(owner="u1", level=LearningLevel.INFERRED_PATTERN, **overrides):
    values = {
        "level": level,
        "exact_behavior": "Use concise replies for this owner",
        "problem": "Repeated replies were abandoned",
        "supporting_event_ids": _event_ids(owner, 3),
        "counterexamples": ["A detailed answer was accepted"],
        "expected_metric_improvement": {
            "metric": "abandonment_rate",
            "minimum_delta": 0.01,
        },
        "risk": "low",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        "rollback_action": "Restore the previous response length",
        "proposed_change": {
            "config_key": "preference.verbosity",
            "value": "concise",
            "consequential": False,
        },
    }
    values.update(overrides)
    return create_learning_candidate(owner, **values)


def _passing_evaluator(stage, candidate):
    del candidate
    return {
        "passed": True,
        "safety_passed": True,
        "score": 0.95,
        "baseline_score": 0.9,
        "metric_delta": 0.05,
        "confidence_low": 0.01,
        "case_count": 20 if stage == "adversarial" else 100,
    }


def _shadow(owner, candidate_id, count=1):
    for index in range(count):
        record_shadow_observation(
            owner,
            candidate_id,
            live_output=f"live {index}",
            candidate_output=f"candidate {index}",
            disagreed=False,
        )


def test_events_are_redacted_and_owner_scoped():
    credential_field = "".join(("pass", "word"))
    event = record_learning_event(
        "u1",
        "explicit_correction",
        text="My private raw correction must not be copied",
        metadata={credential_field: "unit-test-placeholder", "signal": "wrong"},
    )
    stored = list_learning_events("u1")[0]
    assert "private raw correction" not in str(stored)
    assert stored["features"]["content_hash"] == event["features"]["content_hash"]
    assert credential_field not in stored["metadata"]
    assert list_learning_events("u2") == []


def test_session_adaptation_is_expiring_and_session_scoped():
    capture_session_adaptation(
        "u1", "telegram", "For this chat, keep your replies concise"
    )
    assert get_session_adaptation("u1", "telegram")["response_length"] == "concise"
    assert get_session_adaptation("u1", "slack") == {}

    capture_session_adaptation(
        "u1",
        "telegram",
        "For now, when I say dreamview I mean AI Sync Box strip",
    )
    assert get_session_adaptation("u1", "telegram")["device_reference"] == {
        "alias": "dreamview",
        "target": "AI Sync Box strip",
    }
    capture_session_adaptation(
        "u1", "telegram", "For this chat, focus on the kitchen lighting"
    )
    capture_session_adaptation(
        "u1", "telegram", "For this task, only use read-only tools"
    )
    current = get_session_adaptation("u1", "telegram")
    assert current["active_topic"] == "the kitchen lighting"
    assert current["task_constraints"] == "only use read-only tools"
    with pytest.raises(PermissionError, match="authority"):
        capture_session_adaptation(
            "u1", "telegram", "For this task, use admin permissions"
        )
    directives = PersonalityContext({}).build_prompt_directives(
        "What should I change?",
        {"_session_adaptation": current},
    )
    assert any("Session topic" in item for item in directives)
    assert any("never overrides permissions" in item for item in directives)

    state = local_store.get_session_metadata("telegram", "u1")
    item = state["controlled_session_adaptation_v1"]
    item["values"]["response_length"]["expires_at"] = "2000-01-01T00:00:00+00:00"
    local_store.set_session_metadata(
        "telegram", "u1", "controlled_session_adaptation_v1", item
    )
    remaining = get_session_adaptation("u1", "telegram")
    assert "response_length" not in remaining
    assert remaining["device_reference"]["alias"] == "dreamview"


def test_inferred_candidate_never_activates_before_evaluation_shadow_and_approval():
    candidate = _candidate()
    assert candidate["status"] == "pending_evaluation"
    assert get_active_configuration("u1", "preference.verbosity") is None
    with pytest.raises(ValueError, match="shadow"):
        promote_low_risk_candidate(
            "u1", candidate["id"], human_approved=True, minimum_shadow_observations=1
        )

    candidate = run_offline_evaluation("u1", candidate["id"], _passing_evaluator)
    assert candidate["status"] == "shadow"
    _shadow("u1", candidate["id"])
    with pytest.raises(PermissionError, match="owner approval"):
        promote_low_risk_candidate(
            "u1", candidate["id"], human_approved=False, minimum_shadow_observations=1
        )
    promoted = promote_low_risk_candidate(
        "u1", candidate["id"], human_approved=True, minimum_shadow_observations=1
    )
    assert promoted["provenance_event_ids"] == candidate["supporting_event_ids"]
    assert get_active_configuration("u1", "preference.verbosity") == "concise"


def test_deduplicated_candidate_accumulates_independent_evidence():
    event_ids = _event_ids("u1", 3)
    first = _candidate(supporting_event_ids=event_ids[:1])
    assert first["status"] == "insufficient_evidence"
    updated = _candidate(supporting_event_ids=event_ids)
    assert updated["id"] == first["id"]
    assert updated["status"] == "pending_evaluation"
    assert updated["supporting_event_ids"] == event_ids


def test_failed_evaluation_and_rejected_candidates_remain_inspectable():
    candidate = _candidate()

    def failing(stage, document):
        result = _passing_evaluator(stage, document)
        if stage == "adversarial":
            result["safety_passed"] = False
        return result

    failed = run_offline_evaluation("u1", candidate["id"], failing)
    assert failed["status"] == "evaluation_failed"
    rejected = reject_candidate("u1", candidate["id"], "safety regression")
    assert rejected["status"] == "rejected"
    assert list_learning_candidates("u1")[0]["evaluation"]["passed"] is False


def test_learning_cannot_expand_authority_or_cross_owner_scope():
    with pytest.raises(PermissionError, match="authority"):
        _candidate(
            proposed_change={
                "config_key": "permissions.admin",
                "value": True,
            }
        )
    with pytest.raises(PermissionError, match="one owner"):
        _candidate(affected_owners=["u1", "u2"])


def test_level3_requires_full_evaluation_shadow_opt_in_and_canary():
    candidate = _candidate(
        level=LearningLevel.OPTIMIZATION,
        exact_behavior="Raise a routing threshold after validated improvement",
        proposed_change={
            "config_key": "routing.home_control_threshold",
            "value": 0.78,
            "consequential": False,
        },
    )
    with pytest.raises(ValueError, match="shadow"):
        start_canary(
            "u1", candidate["id"], owner_opted_in=True, minimum_shadow_observations=1
        )
    candidate = run_offline_evaluation("u1", candidate["id"], _passing_evaluator)
    _shadow("u1", candidate["id"])
    with pytest.raises(PermissionError, match="opted-in"):
        start_canary(
            "u1", candidate["id"], owner_opted_in=False, minimum_shadow_observations=1
        )
    start_canary(
        "u1", candidate["id"], owner_opted_in=True, minimum_shadow_observations=1
    )
    record_canary_observation("u1", candidate["id"], metric_delta=0.04)
    promoted = promote_canary("u1", candidate["id"], minimum_canary_observations=1)
    assert promoted["status"] == "active"


def test_canary_regression_automatically_rolls_back():
    candidate = _candidate(
        level=LearningLevel.OPTIMIZATION,
        proposed_change={
            "config_key": "routing.alias_weight",
            "value": 1.1,
            "consequential": False,
        },
    )
    candidate = run_offline_evaluation("u1", candidate["id"], _passing_evaluator)
    _shadow("u1", candidate["id"])
    start_canary(
        "u1", candidate["id"], owner_opted_in=True, minimum_shadow_observations=1
    )
    rolled_back = record_canary_observation("u1", candidate["id"], metric_delta=-0.5)
    assert rolled_back["status"] == "rolled_back"
    assert get_active_configuration("u1", "routing.alias_weight") is None


def test_level4_can_only_reach_non_deploying_staged_state():
    candidate = _candidate(
        level=LearningLevel.CODE_OR_SKILL,
        exact_behavior="Propose a provider adapter patch",
        proposed_change={
            "change_type": "code_patch",
            "component": "provider_adapter",
            "summary": "Handle a bounded response shape",
        },
    )
    candidate = run_offline_evaluation("u1", candidate["id"], _passing_evaluator)
    assert candidate["status"] == "awaiting_human_approval"
    with pytest.raises(PermissionError, match="every test"):
        stage_code_candidate(
            "u1",
            candidate["id"],
            isolated_branch="curie/adapter-candidate",
            generated_tests_passed=True,
            human_tests_passed=False,
            security_review_passed=True,
            human_approved=True,
        )
    staged = stage_code_candidate(
        "u1",
        candidate["id"],
        isolated_branch="curie/adapter-candidate",
        generated_tests_passed=True,
        human_tests_passed=True,
        security_review_passed=True,
        human_approved=True,
    )
    assert staged["status"] == "staged"
    assert staged["merge_allowed"] is False
    assert staged["deployment_allowed"] is False


def test_versioned_configuration_has_one_command_rollback():
    first = _candidate()
    first = run_offline_evaluation("u1", first["id"], _passing_evaluator)
    _shadow("u1", first["id"])
    promote_low_risk_candidate(
        "u1", first["id"], human_approved=True, minimum_shadow_observations=1
    )
    second = _candidate(
        exact_behavior="Use balanced replies after newer validated evidence",
        proposed_change={
            "config_key": "preference.verbosity",
            "value": "balanced",
            "consequential": False,
        },
    )
    second = run_offline_evaluation("u1", second["id"], _passing_evaluator)
    _shadow("u1", second["id"])
    promote_low_risk_candidate(
        "u1", second["id"], human_approved=True, minimum_shadow_observations=1
    )
    assert get_active_configuration("u1", "preference.verbosity") == "balanced"
    rollback = rollback_configuration(
        "u1", "preference.verbosity", reason="manual regression check"
    )
    assert rollback["restored"]["version"] == 1
    assert get_active_configuration("u1", "preference.verbosity") == "concise"
    configs = list_adaptive_configs("u1", "preference.verbosity")
    assert {item["status"] for item in configs} == {"active", "rolled_back"}


def test_explicit_preference_reset_disables_promoted_adaptation():
    candidate = _candidate()
    candidate = run_offline_evaluation("u1", candidate["id"], _passing_evaluator)
    _shadow("u1", candidate["id"])
    promote_low_risk_candidate(
        "u1", candidate["id"], human_approved=True, minimum_shadow_observations=1
    )
    assert get_preferences("u1")["verbosity"] == "concise"
    assert "reset" in handle_adaptation_command("u1", "/reset_preferences verbosity")
    assert get_preferences("u1")["verbosity"] == "balanced"
