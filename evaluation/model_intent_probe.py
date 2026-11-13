"""Repeat actual local-model routing decisions; never execute selected tools."""

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

CASES = [
    ("Could you inspect the current RAM pressure on this machine?", "ram_usage"),
    ("Inspect the RAM headroom on this host.", "ram_usage"),
    ("Could you inspect which hardware accelerators this host supports?", "hardware"),
    ("Could you determine whether my desk lamp has power?", "home_status"),
    (
        "Could you determine whether my desk lamp has power, without changing anything?",
        "home_status",
    ),
    (
        "Could you inspect whether my desk lamp has power? Please do not change its state.",
        "home_status",
    ),
    ("Could you retrieve messages in Gmail concerning dinner?", "gmail_search"),
    ("Could you look in Gmail for messages about dinner?", "gmail_search"),
    ("Could you obtain the current operational telemetry?", "dashboard_snapshot"),
]
HELD_OUT = [
    ("Could you examine available RAM on the server?", "ram_usage"),
    ("Could you identify the hardware available for inference?", "hardware"),
    ("Please inspect whether the bedside lamp has power.", "home_status"),
    ("Could you retrieve Gmail messages about the invoice?", "gmail_search"),
    ("Could you obtain Curie's telemetry right now?", "dashboard_snapshot"),
]


async def run(report, rounds, held_out=False):
    import agent.intent_router as router

    report = Path(report)
    report.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    original = router._ask_intent_model
    raw = []

    async def observed(prompt):
        result = await original(prompt)
        raw.append(result)
        return result

    with patch.object(router, "_ask_intent_model", side_effect=observed):
        for round_number in range(1, rounds + 1):
            for text, expected in HELD_OUT if held_out else CASES:
                assert (
                    router.classify_request(text) is None
                ), "This probe must reach the model"
                raw.clear()
                start = time.monotonic()
                result = await router.resolve_request(text)
                row = {
                    "round": round_number,
                    "prompt": text,
                    "expected": expected,
                    "decision": asdict(result) if result else None,
                    "model_output": list(raw),
                    "seconds": round(time.monotonic() - start, 2),
                    "passed": bool(
                        result
                        and result.action == expected
                        and result.source == "model"
                    ),
                }
                if expected == "home_status":
                    row["passed"] = row["passed"] and result.params.get(
                        "target", ""
                    ).casefold() == ("bedside lamp" if held_out else "desk lamp")
                if expected == "gmail_search":
                    row["passed"] = (
                        row["passed"]
                        and ("invoice" if held_out else "dinner")
                        in result.params.get("query", "").casefold()
                    )
                rows.append(row)
                print(json.dumps(row), flush=True)
                report.write_text(
                    json.dumps({"complete": False, "rows": rows}, indent=2)
                )
    report.write_text(
        json.dumps(
            {
                "complete": True,
                "tools_executed": False,
                "passed": sum(row["passed"] for row in rows),
                "total": len(rows),
                "rows": rows,
            },
            indent=2,
        )
    )
    return all(row["passed"] for row in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--held-out", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv()
    os.environ["INTENT_LLM_ENABLED"] = "true"
    os.environ["LLM_PROVIDER_PRIORITY"] = "llama.cpp"
    raise SystemExit(
        0 if asyncio.run(run(args.report, args.rounds, args.held_out)) else 1
    )


if __name__ == "__main__":
    main()
