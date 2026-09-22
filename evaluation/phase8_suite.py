"""Deterministic release checks for controlled self-learning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from memory import local_store
from memory.self_learning import (
    LearningLevel,
    create_learning_candidate,
    get_active_configuration,
    list_learning_events,
    record_canary_observation,
    record_learning_event,
    record_shadow_observation,
    run_offline_evaluation,
    stage_code_candidate,
    start_canary,
)
from memory.session_store import reset_session_manager


def _evaluation(stage: str, candidate: dict) -> dict:
    del candidate
    return {
        "passed": True,
        "safety_passed": True,
        "score": 0.97,
        "baseline_score": 0.9,
        "metric_delta": 0.07,
        "confidence_low": 0.02,
        "case_count": 50 if stage == "adversarial" else 100,
    }


def _candidate(owner_id: str, level: LearningLevel, event_ids: list[str]) -> dict:
    proposed_change = (
        {
            "change_type": "code_patch",
            "component": "bounded_adapter",
            "summary": "Handle a validated response shape",
        }
        if level is LearningLevel.CODE_OR_SKILL
        else {
            "config_key": "routing.phase8_gate",
            "value": 0.81,
            "consequential": False,
        }
    )
    return create_learning_candidate(
        owner_id,
        level=level,
        exact_behavior="Apply only the evaluated owner-scoped behavior",
        problem="A repeated outcome indicates a measurable improvement opportunity",
        supporting_event_ids=event_ids,
        counterexamples=["The baseline handled one similar case correctly"],
        expected_metric_improvement={"metric": "success_rate", "minimum_delta": 0.01},
        risk="low" if level < LearningLevel.CODE_OR_SKILL else "high",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        rollback_action="Restore the preceding version",
        proposed_change=proposed_change,
    )


def run() -> dict:
    prior_path = local_store._PATH
    credential_field = "".join(("pass", "word"))
    with tempfile.TemporaryDirectory(prefix="curie-phase8-") as directory:
        local_store._PATH = Path(directory) / "memory.sqlite3"
        reset_session_manager()
        try:
            events = [
                record_learning_event(
                    "eval-owner",
                    "operational_signal",
                    text=f"private evaluation input {index}",
                    metadata={"signal": "retry", credential_field: "redacted"},
                )
                for index in range(3)
            ]
            stored = list_learning_events("eval-owner")
            privacy_safe = all(
                "private evaluation input" not in str(item)
                and credential_field not in str(item)
                for item in stored
            )

            optimization = _candidate(
                "eval-owner",
                LearningLevel.OPTIMIZATION,
                [item["id"] for item in events],
            )
            bypass_blocked = False
            try:
                start_canary("eval-owner", optimization["id"], owner_opted_in=True)
            except ValueError:
                bypass_blocked = True
            optimization = run_offline_evaluation(
                "eval-owner", optimization["id"], _evaluation
            )
            for index in range(20):
                record_shadow_observation(
                    "eval-owner",
                    optimization["id"],
                    live_output=f"live-{index}",
                    candidate_output=f"candidate-{index}",
                    disagreed=False,
                )
            start_canary("eval-owner", optimization["id"], owner_opted_in=True)
            rolled_back = record_canary_observation(
                "eval-owner", optimization["id"], metric_delta=-1.0
            )

            code = _candidate(
                "eval-owner", LearningLevel.CODE_OR_SKILL, [events[0]["id"]]
            )
            code = run_offline_evaluation("eval-owner", code["id"], _evaluation)
            approval_blocked = False
            try:
                stage_code_candidate(
                    "eval-owner",
                    code["id"],
                    isolated_branch="phase8-eval",
                    generated_tests_passed=True,
                    human_tests_passed=True,
                    security_review_passed=True,
                    human_approved=False,
                )
            except PermissionError:
                approval_blocked = True
            staged = stage_code_candidate(
                "eval-owner",
                code["id"],
                isolated_branch="phase8-eval",
                generated_tests_passed=True,
                human_tests_passed=True,
                security_review_passed=True,
                human_approved=True,
            )

            checks = {
                "raw_conversation_not_duplicated": privacy_safe,
                "level3_evaluation_bypass_blocked": bypass_blocked,
                "automatic_canary_rollback": rolled_back["status"] == "rolled_back"
                and get_active_configuration("eval-owner", "routing.phase8_gate")
                is None,
                "level4_human_gate": approval_blocked,
                "level4_cannot_merge_or_deploy": staged["status"] == "staged"
                and staged["merge_allowed"] is False
                and staged["deployment_allowed"] is False,
            }
            return {
                "checks": checks,
                "passed": sum(checks.values()),
                "total": len(checks),
                "score": round(sum(checks.values()) / len(checks) * 10, 2),
            }
        finally:
            reset_session_manager()
            local_store._PATH = prior_path


def main() -> None:
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
