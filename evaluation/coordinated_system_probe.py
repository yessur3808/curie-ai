"""Live two-model handoff evaluation, without external tools or personal memory.

Uses production model configuration, synthetic evidence and temporary telemetry.
Invoke with --report PATH. Run after the ordinary contextual_assistant_probe.
"""

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
import tempfile
import time
import uuid


async def run(report):
    from agent.orchestration.model_service import ModelConversationService
    from llm import manager
    from llm.chat_prompt import conversation_prompt
    from llm.inference_service import close_inference_service

    service = ModelConversationService(manager)
    run_id = uuid.uuid4().hex
    cases = [
        (
            "casual",
            [],
            "Long day. Tea and a book or more debugging? Pick one, keep it casual.",
            [],
        ),
        (
            "selective_review",
            [],
            "Independently verify this design: our API median is 0.8s and p95 is 14s. "
            "Should we replace the database first? Give one measurement to take before changing it.",
            [],
        ),
        (
            "evidence_handoff",
            [
                ("user", "Turn off the desk lamp"),
                ("assistant", "The desk lamp is off."),
                ("user", "Is it off?"),
                ("assistant", "I checked; it is off."),
            ],
            "Independently verify why you turned it off. Do not do it again.",
            [
                {
                    "id": "sandbox-action",
                    "source_turn": "synthetic-1",
                    "outcome": {
                        "status": "verified",
                        "capabilities": ["home_control"],
                        "request": "Turn off the desk lamp",
                        "response": "Turned Desk Lamp off.",
                    },
                }
            ],
        ),
        (
            "draft_is_not_action",
            [],
            "Draft a short note to Nora saying I will be late. Don't send it. Independently check the wording.",
            [],
        ),
    ]
    rows = []
    cases.append(
        (
            "deep_review",
            [],
            "Give me a thorough comparison of cache invalidation versus adding replicas for stale reads. "
            "State the first diagnostic and one caveat in two sentences.",
            [],
        )
    )
    try:
        for name, history, user, evidence in cases:
            prompt = conversation_prompt(
                "You are Curie, a capable, warm, casual assistant with subtle French flair. "
                "Only application tool evidence verifies external actions. No tools are available here.",
                history,
                user,
            ).with_evidence(evidence)
            started = time.monotonic()
            result = await service.generate(
                prompt, user, 0.3, owner_id="coordination-eval-" + run_id + "-" + name
            )
            meta = result.metadata.get("coordination", {})
            delegated = name != "casual"
            checks = {
                "selective_mode": meta.get("mode")
                == ("specialist_review" if delegated else "direct"),
                "bounded_calls": meta.get("model_calls") == (2 if delegated else 1),
                "verification_passed": meta.get("verification")
                in (
                    {"verified", "corrected"}
                    if delegated
                    else {"deterministic_checked"}
                ),
                "has_answer": bool(result.text.strip())
                and not result.text.startswith("[Error"),
            }
            if name == "evidence_handoff":
                checks["cites_correct_evidence"] = meta.get("evidence_ids") == [
                    "sandbox-action"
                ]
                checks["attributes_request"] = bool(
                    re.search(
                        r"\byou\s+(?:(?:explicitly|specifically|directly|originally)\s+)?(?:asked|told)\b|\byour request\b",
                        result.text,
                        re.I,
                    )
                )
                checks["separate_verifier_role"] = meta.get("roles") == [
                    "reasoning",
                    "critic",
                ]
            if name == "deep_review":
                checks["selects_deep_role"] = meta.get("roles") == [
                    "deep_reasoning",
                    "critic",
                ]
            row = {
                "case": name,
                "user": user,
                "answer": result.text,
                "seconds": round(time.monotonic() - started, 2),
                "coordination": meta,
                "checks": checks,
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            Path(report).write_text(
                json.dumps(
                    {"complete": False, "cases": rows}, ensure_ascii=False, indent=2
                )
            )
            if not all(checks.values()):
                break
    finally:
        await close_inference_service()
    result = {
        "complete": len(rows) == len(cases),
        "cases": rows,
        "passed": all(all(r["checks"].values()) for r in rows),
    }
    Path(report).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result["passed"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv()
    with tempfile.TemporaryDirectory(prefix="curie-coordination-eval-") as folder:
        for key, name in (
            ("CURIE_LOCAL_MEMORY_DB", "memory.sqlite3"),
            ("CURIE_RESOURCE_BUDGET_PATH", "budgets.json"),
            ("CURIE_DIAGNOSTICS_PATH", "diagnostics.json"),
            ("CURIE_PIPELINE_ROLLOUT_STATE", "rollout.json"),
        ):
            os.environ[key] = str(Path(folder) / name)
        os.environ["LLM_ENSEMBLE_ENABLED"] = "true"
        os.environ["LLM_PROVIDER_PRIORITY"] = "llama.cpp"
        os.environ["CURIE_REMOTE_MODELS_ENABLED"] = "false"
        if not asyncio.run(run(args.report)):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
