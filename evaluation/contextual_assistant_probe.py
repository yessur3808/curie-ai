"""Opt-in live-model probe of Telegram's real workflow, with sandboxed tools.

Run as a module from the repository. Temporary owner/storage are destroyed on exit.
No production identity, credentials, device, calendar, or connector delivery is used.
Scores are behavioral checks, not a claim of human-rated conversational quality.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

FLOWS = {
    "error_recovery": [
        "Search Twitter for current local AI setup news",
        "What is the error?",
        "You previously mentioned the X search tool failed. Is that a login problem or something else?",
        "What can we do instead without paying X? Just one useful option.",
        "Different topic: I'm choosing dinner. Lentil soup or noodles, after a heavy lunch? Pick one.",
    ],
    "reference_editing": [
        "Draft two short notes to Nora: one apologizing for being late, one thanking her for waiting. Label them A and B. Don't send anything.",
        "Make only B warmer, not romantic. Keep Nora's name.",
        "Which note did I ask you to change?",
        "New topic: explain why the sky is blue in one sentence.",
    ],
    "tool_followup": [
        "What's the weather in Kyoto?",
        "Would I need an umbrella there?",
        "Actually I meant Osaka. Check that instead.",
        "Which city did you check most recently?",
    ],
    "device_flow": [
        "Turn off the desk lamp",
        "Is it off now?",
        "Why did you turn it off?",
        "You said 'turn off the desk lamp'. I'm asking about that, not asking you to do it again.",
    ],
    "reasoned_opinion": [
        "The meeting is tomorrow, but a test heater is overheating right now. What gets my attention first? No tools, just advice.",
        "Why that rather than the meeting?",
        "Our API median is 0.8 seconds, p95 is 14 seconds. Would replacing the database be your first step? Give a candid short answer.",
        "What would you measure first then?",
    ],
}


def behavioral_checks(row):
    """Objective floor only: passing these is not a conversational-quality score."""
    key = (row["flow"], row["turn"])
    expected_calls = {
        ("error_recovery", 1): ["x_search"],
        ("tool_followup", 1): ["weather"],
        ("tool_followup", 3): ["weather"],
        ("device_flow", 1): ["home_control"],
        ("device_flow", 2): ["home_status"],
    }
    reply = str(row.get("assistant") or "")
    checks = {
        "has_answer": bool(reply.strip()) and not reply.startswith("[Error"),
        "only_expected_tool_calls": [c["capability"] for c in row["tool_calls"]]
        == expected_calls.get(key, []),
    }
    if key in expected_calls:
        expected_status = "failed" if key == ("error_recovery", 1) else "completed"
        checks["expected_execution_status"] = (
            row.get("execution_status") == expected_status
        )
    if key in {("error_recovery", 2), ("error_recovery", 3)}:
        checks["retains_credit_error"] = "credit" in reply.casefold()
    if key == ("reference_editing", 1):
        checks["short_drafts"] = len(reply.split()) <= 80
        checks["recipient_preserved"] = "Nora" in reply
    if key == ("reference_editing", 2):
        checks["short_revision"] = len(reply.split()) <= 60
        checks["recipient_preserved"] = "Nora" in reply
    if key == ("tool_followup", 3):
        checks["corrected_city_lookup"] = any(
            "osaka" in str(call["parameters"]).casefold() for call in row["tool_calls"]
        )
    if key == ("tool_followup", 4):
        checks["latest_city_reference"] = "osaka" in reply.casefold()
    return checks


async def run_probe(report):
    from agent.chat_workflow import ChatWorkflow
    from agent.tooling import get_runtime_registry
    from agent.tooling.contracts import ToolResult
    from agent.tooling.errors import ProviderAccessError
    from memory import local_store
    from memory.session_store import get_session_manager

    registry = get_runtime_registry()
    calls = []
    state = {"lamp": "on"}

    async def external_boundary(name, params, context):
        calls.append({"capability": name, "parameters": dict(params)})
        if name == "x_search":
            raise ProviderAccessError(
                provider="X",
                status=402,
                code="credits_depleted",
                user_message="X returned HTTP 402: API credits depleted. I couldn't retrieve posts. This is not a login error. I can search public web news instead.",
            )
        if name == "weather":
            city = "Osaka" if "osaka" in str(params).lower() else "Kyoto"
            return ToolResult(
                f"Weather for {city}: rain, 18°C. (Test fixture)",
                {"city": city, "condition": "rain", "temperature": 18},
                "sandbox weather provider",
            )
        if name in {"home_control", "home_status"}:
            if name == "home_control":
                state["lamp"] = params.get("state", "off")
            action = "Turned" if name == "home_control" else "Checked"
            return ToolResult(
                f"{action} Desk Lamp: {state['lamp']}.",
                {
                    "verification_status": "verified",
                    "devices": [{"name": "Desk Lamp", "state": state["lamp"]}],
                    "verified": True,
                },
                "sandbox device",
            )
        raise PermissionError("External capabilities are disabled in this evaluation")

    results = []
    with ExitStack() as stack:
        stack.enter_context(
            patch("cli.tasks.TASKS_FILE", local_store._PATH.parent / "tasks.json")
        )
        stack.enter_context(
            patch.object(registry, "execute", side_effect=external_boundary)
        )
        stack.enter_context(
            patch(
                "agent.chat_workflow.UserManager.get_user_profile",
                return_value={"timezone": "Asia/Hong_Kong"},
            )
        )
        for flow, turns in FLOWS.items():
            owner = "evaluation-" + flow
            workflow = ChatWorkflow(max_history=8)
            workflow.learning_service.submit = lambda *a, **k: None
            for index, text in enumerate(turns):
                before = len(calls)
                started = time.monotonic()
                result = await workflow.process_message(
                    {
                        "platform": "telegram",
                        "internal_id": owner,
                        "external_user_id": owner,
                        "external_chat_id": owner,
                        "message_id": f"{flow}-{index}",
                        "text": text,
                        "timestamp": datetime.now(timezone.utc),
                    }
                )
                row = {
                    "flow": flow,
                    "turn": index + 1,
                    "user": text,
                    "assistant": result.get("text"),
                    "model": result.get("model_used"),
                    "seconds": round(time.monotonic() - started, 2),
                    "execution_status": result.get("execution_status"),
                    "routing": result.get("routing"),
                    "tool_calls": calls[before:],
                }
                row["behavioral_checks"] = behavioral_checks(row)
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                # Preserve partial evidence even if the native inference backend exits.
                Path(report).write_text(
                    json.dumps(
                        {
                            "tool_mode": "sandboxed_external_boundary",
                            "complete": False,
                            "transcript": results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            get_session_manager().reset_session("telegram", owner)
    Path(report).write_text(
        json.dumps(
            {
                "tool_mode": "sandboxed_external_boundary",
                "complete": True,
                "transcript": results,
                "temporary_database": str(local_store._PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--flow", choices=FLOWS)
    args = parser.parse_args()
    if args.flow:
        selected = FLOWS[args.flow]
        FLOWS.clear()
        FLOWS[args.flow] = selected
    from dotenv import load_dotenv

    load_dotenv()
    with tempfile.TemporaryDirectory(prefix="curie-flow-probe-") as folder:
        os.environ.update(
            {
                "CURIE_LOCAL_MEMORY_DB": folder + "/memory.sqlite3",
                "CURIE_PIPELINE_ROLLOUT_STATE": folder + "/rollout.json",
                "CURIE_TURN_TRACE_PATH": folder + "/turns.jsonl",
                "CURIE_CREDENTIAL_STORE": folder + "/credentials.enc",
                "CURIE_RESOURCE_BUDGET_PATH": folder + "/budgets.json",
                "CURIE_DIAGNOSTICS_PATH": folder + "/diagnostics.json",
                "LLM_PROVIDER_PRIORITY": "llama.cpp",
                "POSTGRES_HOST": "",
                "MONGODB_URI": "",
                "INTENT_LLM_ENABLED": "false",
            }
        )
        from memory import local_store

        local_store._PATH = Path(folder) / "memory.sqlite3"
        from agent.observability import reset_telemetry

        reset_telemetry(path=Path(folder) / "telemetry.json")
        asyncio.run(run_probe(args.report))


if __name__ == "__main__":
    main()
