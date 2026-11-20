"""Re-score saved synthetic conversations without inference or rewriting evidence.

Usage: python -m evaluation.review_conversation_reports report.json [report.json ...]
Checks are a regression floor, not a semantic or naturalness score.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.contextual_assistant_probe import behavioral_checks


def review_report(report: dict) -> dict:
    rows = report.get("transcript", [])
    failures = []
    for row in rows:
        failed = [name for name, passed in behavioral_checks(row).items() if not passed]
        if failed:
            failures.append(
                {"flow": row["flow"], "turn": row["turn"], "checks": failed}
            )
    return {
        "complete": report.get("complete") is True,
        "turns": len(rows),
        "failures": failures,
        "passed": report.get("complete") is True and bool(rows) and not failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    summaries = {
        str(path): review_report(json.loads(path.read_text())) for path in args.reports
    }
    print(json.dumps(summaries, indent=2))
    raise SystemExit(0 if all(item["passed"] for item in summaries.values()) else 1)


if __name__ == "__main__":
    main()
