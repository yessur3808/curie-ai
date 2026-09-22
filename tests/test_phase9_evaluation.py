import json
from pathlib import Path

import pytest

from evaluation.contracts import load_catalog
from evaluation.gates import evaluate_gates, load_gate_config, release_blocked
from evaluation.graders import CalibratedSubjectiveGrader, grade_case
from evaluation.model_comparison import (
    ComparisonConditions,
    ModelRun,
    compare_runs,
)
from evaluation.release import run_release
from evaluation.runners import CanaryPolicyError, validate_canary_plan
from evaluation.tool_provider_simulator import ProviderMode, run_provider_matrix

ROOT = Path(__file__).resolve().parents[1]


def _case():
    return next(
        case
        for dataset in load_catalog(ROOT / "evaluation/datasets")
        for case in dataset["cases"]
        if case["id"] == "conversation.topic-change.clean.v1"
    )


def test_catalog_has_versioned_cases_for_every_required_taxonomy():
    datasets = load_catalog(ROOT / "evaluation/datasets")
    cases = [case for dataset in datasets for case in dataset["cases"]]
    tags = {tag for case in cases for tag in case["tags"]}
    assert len(datasets) == 10
    assert len(cases) >= 18
    assert {
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
    } <= tags
    assert sum("production_regressions" in case["tags"] for case in cases) >= 9


def test_deterministic_grader_checks_tools_facts_style_and_secrets():
    case = _case()
    expected = case["expected"]
    observation = {
        "route": dict(expected["route"]),
        "entities": dict(expected["entities"]),
        "plan": {"constraints": expected["plan"]["required_constraints"]},
        "tool_calls": expected["tool_calls"]["required"],
        "verification": dict(expected["verification"]),
        "response": {"text": "Dinner sounds good."},
    }
    assert grade_case(case, observation).passed

    observation["tool_calls"] = ["home_control"]
    observation["response"] = {"text": "".join(("pass", "word=", "placeholder"))}
    result = grade_case(case, observation)
    assert not result.passed
    assert "forbidden tool call occurred" in result.failures
    assert "response contains a secret-like value" in result.failures


def test_subjective_grader_requires_human_calibration_and_cannot_gate_security():
    grader = CalibratedSubjectiveGrader(minimum_samples=3)
    with pytest.raises(RuntimeError):
        grader.accept("naturalness", 4.8, threshold=4.5)
    calibration = grader.calibrate([(5, 5), (4, 4.2), (3, 3.1)])
    assert calibration.calibrated
    assert grader.accept("naturalness", 4.8, threshold=4.5)
    with pytest.raises(ValueError):
        grader.accept("security", 5, threshold=4.5)


def test_release_gate_configuration_is_complete_and_missing_metrics_fail_closed():
    config = load_gate_config(ROOT / "evaluation/release_gates.json")
    assert len(config["gates"]) == 24
    assert all(gate["blocking"] for gate in config["gates"].values())
    results = evaluate_gates(config, {})
    assert release_blocked(results)
    assert all(not result.passed for result in results)


def test_model_comparison_rejects_changed_prompts_or_datasets():
    one = ComparisonConditions("p1", "c1", "s1", {"temperature": 0}, "cpu", 2, ("d1",))
    two = ComparisonConditions("p2", "c1", "s1", {"temperature": 0}, "cpu", 2, ("d1",))
    cases = ({"case_id": "a", "accuracy": 1},)
    with pytest.raises(ValueError):
        compare_runs((ModelRun("one", one, cases), ModelRun("two", two, cases)))


def test_credentialed_canaries_are_explicit_sandboxed_and_never_public_by_default():
    harmless = {
        "capabilities": ["read_health"],
        "sandbox_resources": ["evaluation-only-home-assistant"],
        "cleanup_required": True,
    }
    with pytest.raises(CanaryPolicyError):
        validate_canary_plan(harmless)
    assert validate_canary_plan(harmless, explicitly_enabled=True)["enabled"]
    with pytest.raises(CanaryPolicyError):
        validate_canary_plan(
            {**harmless, "capabilities": ["public_post"]},
            explicitly_enabled=True,
        )


def test_provider_matrix_covers_failures_replay_partial_groups_and_cancellation():
    observations = {item.mode: item for item in run_provider_matrix()}
    assert set(observations) == set(ProviderMode)
    assert observations[ProviderMode.TIMEOUT].verification_status == "unverified"
    assert observations[ProviderMode.RATE_LIMITED].mutation_count == 0
    assert observations[ProviderMode.AUTH_EXPIRED].mutation_count == 0
    assert (
        observations[ProviderMode.CONTRADICTORY_READBACK].verification_status
        == "contradicted"
    )
    assert (
        observations[ProviderMode.PARTIAL_GROUP].execution_status == "partial_failure"
    )
    assert observations[ProviderMode.RECONNECT_REPLAY].replay_suppressed
    assert observations[ProviderMode.CANCELLED].mutation_count == 0


def test_complete_offline_release_report_has_stage_taxonomy_and_blocking_gates():
    report = run_release(ROOT, include_tests=False)
    assert report["offline"] is True
    assert report["release_blocked"] is False
    assert report["tests"]["status"] == "skipped"
    assert report["tests"]["passed"] is None
    assert report["summary"] == {
        "gate_count": 24,
        "passed": 24,
        "waived": 0,
        "blocking_failures": 0,
    }
    assert set(report["per_stage"]) >= {
        "understanding_planning",
        "response_memory",
        "tool_simulation",
        "delivery",
        "connector_end_to_end",
    }
    assert report["per_taxonomy"]["production_regressions"] >= 9
    assert report["failures"] == []


def test_report_contract_does_not_store_raw_test_output_on_success(tmp_path):
    report = run_release(ROOT, include_tests=False)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["tests"]["failure_tail"] == ""
