"""One-command offline evaluation and blocking release-gate runner."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping

from evaluation.contracts import FailureRecord
from evaluation.gates import evaluate_gates, load_gate_config, release_blocked
from evaluation.phase9_suite import ROOT, run as run_phase9
from utils.redaction import redact_secrets


def _safe_version(value: str, fallback: str) -> str:
    value = str(value or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_.:/@+-]{1,120}", value) else fallback


def _feature_flags() -> tuple[str, ...]:
    values = str(os.getenv("CURIE_FEATURE_FLAGS") or "").split(",")
    return tuple(
        value.strip()
        for value in values
        if re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value.strip())
    )


def _run_tests(root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    output = process.stdout + process.stderr

    def count(label: str) -> int:
        matches = re.findall(rf"(\d+)\s+{label}\b", output)
        return sum(int(item) for item in matches)

    return redact_secrets(
        {
            "status": "passed" if process.returncode == 0 else "failed",
            "exit_code": process.returncode,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "passed": count("passed"),
            "failed": count("failed"),
            "deselected": count("deselected"),
            "failure_tail": (
                "\n".join(output.splitlines()[-30:]) if process.returncode else ""
            ),
        }
    )


def _baseline_gate_status(path: str | Path | None) -> dict[str, bool]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return redact_secrets(
        {
            str(item["name"]): bool(item["passed"])
            for item in payload.get("gates", ())
            if isinstance(item, Mapping) and item.get("name")
        }
    )


def run_release(
    root: str | Path = ROOT,
    *,
    include_tests: bool = True,
    baseline: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(root)
    test_report = (
        _run_tests(root)
        if include_tests
        else {
            "status": "skipped",
            "exit_code": None,
            "duration_seconds": 0.0,
            "passed": None,
            "failed": None,
            "deselected": None,
            "failure_tail": "",
        }
    )
    suite = run_phase9(root)
    if test_report["status"] == "failed":
        suite["metrics"]["correctness_pass_rate"] = 0.0
        suite["metrics"]["task_success_rate"] = 0.0

    config = load_gate_config(root / "evaluation/release_gates.json")
    gates = evaluate_gates(config, suite["metrics"])
    baseline_status = _baseline_gate_status(baseline)
    model_version = _safe_version(
        os.getenv("CURIE_EVAL_MODEL_VERSION", ""), "deterministic-runtime"
    )
    prompt_version = _safe_version(
        os.getenv("CURIE_EVAL_PROMPT_VERSION", ""), "repository-current"
    )
    flags = _feature_flags()
    failures = []
    for gate in gates:
        if gate.passed or gate.waived:
            continue
        failures.append(
            FailureRecord(
                case_id=f"gate.{gate.name}",
                taxonomy=gate.dataset.rsplit("/", 1)[-1],
                stage="release_gate",
                expected={"operator": gate.operator, "threshold": gate.threshold},
                actual={"metric": gate.metric, "value": gate.actual},
                model_version=model_version,
                prompt_version=prompt_version,
                feature_flags=flags,
                baseline_status=(
                    "passed"
                    if baseline_status.get(gate.name)
                    else "failed" if gate.name in baseline_status else "not_provided"
                ),
                minimal_reproduction="python -m evaluation.phase9_suite",
                owning_module=gate.component,
            ).as_dict()
        )

    blocked = release_blocked(gates) or test_report["status"] == "failed"
    return redact_secrets(
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "offline": True,
            "release_blocked": blocked,
            "runtime": {
                "model_version": model_version,
                "prompt_version": prompt_version,
                "feature_flags": list(flags),
            },
            "tests": test_report,
            "metrics": suite["metrics"],
            "per_stage": suite["per_stage"],
            "per_taxonomy": suite["per_taxonomy"],
            "catalog": suite["catalog"],
            "gates": [gate.as_dict() for gate in gates],
            "summary": {
                "gate_count": len(gates),
                "passed": sum(gate.passed for gate in gates),
                "waived": sum(gate.waived for gate in gates),
                "blocking_failures": sum(
                    gate.blocking and not gate.passed and not gate.waived
                    for gate in gates
                ),
            },
            "failures": failures,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip pytest when it has already run in the same CI job.",
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "evaluation/reports/latest.json",
    )
    args = parser.parse_args()
    report = run_release(
        ROOT,
        include_tests=not args.skip_tests,
        baseline=args.baseline,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "release_blocked": report["release_blocked"],
                "tests": {
                    "status": report["tests"]["status"],
                    "passed": report["tests"]["passed"],
                    "failed": report["tests"]["failed"],
                    "deselected": report["tests"]["deselected"],
                },
                "summary": report["summary"],
                "report": str(args.report),
            },
            indent=2,
        )
    )
    if report["tests"]["failure_tail"]:
        print(report["tests"]["failure_tail"])
    return 1 if report["release_blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
