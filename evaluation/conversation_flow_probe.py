"""Opt-in local-model conversation sampling with isolated synthetic memory."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

PROMPTS = [
    "Let's plan a fictional Saturday picnic. There are four of us, the budget is 80 dollars total, and Mira cannot eat peanuts. Keep replies brief.",
    "Let's choose a riverside spot and bring sandwiches. What should we avoid in the food?",
    "Correction: there will be six of us, not four. The total budget stays the same.",
    "I finally found the picnic blanket under a mountain of laundry. A small victory.",
    "What is our budget per person now?",
    "Random topic: why do cats love cardboard boxes? One short answer.",
    "And why do they sit in boxes that are obviously too small?",
    "Back to the picnic: how many people are coming, what is the total budget, and what food restriction matters?",
    "Change the picnic budget to 96 dollars total. Everything else stays the same.",
    "Completely different subject: give my fictional spaceship a short, understated name.",
    "I like Kestrel. That is the ship's name now. No need for more suggestions.",
    "Quick detour: explain why the sky is blue in one sentence.",
    "One more detour: tea or coffee for a quiet evening? Pick one.",
    "Back to our picnic plan: tell me the headcount, total budget, budget per person, location type, and food restriction. If you have lost a detail, say so rather than guessing.",
    "What did we name the spaceship? Keep it short.",
    "I'm overwhelmed today. Please be serious and give me one small practical step, without jokes.",
    "A fictional dashboard check failed; no cause was supplied. Explain what we know in one sentence without inventing a reason.",
]


async def run(report):
    from agent.chat_workflow import ChatWorkflow
    from agent.tooling import get_runtime_registry
    from llm.inference_service import close_inference_service

    calls = []

    async def blocked(name, params, context):
        calls.append({"name": name, "params": dict(params)})
        raise PermissionError("All tools blocked in isolated conversation sampling")

    rows = []
    with (
        patch(
            "agent.chat_workflow.UserManager.get_user_profile",
            return_value={"timezone": "UTC"},
        ),
        patch("agent.chat_workflow._TASK_TRACKING", False),
        patch("memory.adaptive.get_relevant_memories", return_value=[]),
        patch("memory.adaptive.get_matching_abilities", return_value=[]),
        patch("memory.adaptive.get_pending_memory_conflicts", return_value=[]),
        patch("services.research_followup.needs_research", return_value=False),
        patch.object(get_runtime_registry(), "execute", side_effect=blocked),
    ):
        workflow = ChatWorkflow()
        workflow.learning_service.submit = lambda *a, **kw: None
        try:
            for index, prompt in enumerate(PROMPTS, 1):
                started = time.monotonic()
                result = await asyncio.wait_for(
                    workflow.process_message(
                        {
                            "platform": "telegram",
                            "internal_id": "synthetic-flow-1457",
                            "external_user_id": "synthetic-flow-1457",
                            "external_chat_id": "synthetic-flow-1457",
                            "message_id": str(index),
                            "text": prompt,
                        }
                    ),
                    timeout=60,
                )
                row = {
                    "turn": index,
                    "user": prompt,
                    "assistant": result.get("text"),
                    "model": result.get("model_used"),
                    "seconds": round(time.monotonic() - started, 2),
                }
                rows.append(row)
                print(json.dumps(row), flush=True)
                report.write_text(
                    json.dumps(
                        {
                            "complete": index == len(PROMPTS),
                            "max_history": workflow.max_history,
                            "learning": "disabled",
                            "tools": "blocked",
                            "tool_attempts": calls,
                            "turns": rows,
                        },
                        indent=2,
                    )
                )
        finally:
            await close_inference_service()


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(".env")
    report = Path(
        "evaluation/reports/capability-awareness-2026-11-01/tone-flow-1457.json"
    )
    if report.exists():
        raise SystemExit("Refusing to overwrite prior evidence")
    with tempfile.TemporaryDirectory(prefix="curie-flow-1457-") as folder:
        os.environ.update(
            {
                "CURIE_LOCAL_MEMORY_DB": folder + "/memory.sqlite3",
                "CURIE_PIPELINE_ROLLOUT_STATE": folder + "/rollout.json",
                "CURIE_TURN_TRACE_PATH": folder + "/turns.jsonl",
                "CURIE_CREDENTIAL_STORE": folder + "/credentials.enc",
                "CURIE_RESOURCE_BUDGET_PATH": folder + "/budgets.json",
                "CURIE_DIAGNOSTICS_PATH": folder + "/diagnostics.json",
                "POSTGRES_HOST": "",
                "MONGODB_URI": "",
                "LLM_PROVIDER_PRIORITY": "llama.cpp",
                "INTENT_LLM_ENABLED": "false",
            }
        )
        asyncio.run(run(report))
