"""Sandboxed live-workflow probe for natural capability awareness.

The prompts deliberately avoid slash commands and registered capability names.
External execution is replaced with deterministic fixtures, so the probe never
touches real accounts, devices, calendars, email, analytics, or network sources.
With --live-read-only, only host memory/hardware, dashboard health and public weather
execute against real sources; all other tool execution is blocked.
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
import uuid
from unittest.mock import patch

FLOWS = {
    "calendar": [
        ("Have I got anything scheduled for tomorrow?", "calendar_list", "Dentist"),
        ("Could you check what I've got on tomorrow?", "calendar_list", "Dentist"),
        ("Have a look at my calendar for tomorrow.", "calendar_list", "Dentist"),
    ],
    "calendar_draft": [
        (
            "Schedule a dentist appointment tomorrow at 9:30 am on my calendar",
            None,
            "approve",
        ),
    ],
    "weather_followup": [
        (
            "Could you see whether it will rain in Osaka tomorrow?",
            "weather",
            "Osaka",
        ),
        ("Actually I meant Kyoto. Check that instead.", "weather", "Kyoto"),
    ],
    "weather_chain": [
        (
            "Could you check whether it will rain in NYC tomorrow?",
            "weather",
            "New York",
        ),
        ("Actually I meant Kyoto. Check that instead.", "weather", "Kyoto"),
        ("Actually I meant Osaka. Check that instead.", "weather", "Osaka"),
    ],
    "gather": [
        (
            "Could you pull together the latest local AI news?",
            "research",
            "local AI news",
        ),
        (
            "Take a quick look at how my Atmosphere website has been doing lately.",
            "observatory_project_analytics",
            "128 page views",
        ),
        ("Now check whether Curie itself is healthy.", "dashboard_snapshot", "healthy"),
        (
            "Find me a coffee shop near Central that's open now.",
            "places_search",
            "Central Coffee",
        ),
        ("Where is flight CX101 right now?", "flight_track", "en route"),
        (
            "Gather the latest local AI news from the public web.",
            "research",
            "local AI news",
        ),
        (
            "Could you find the latest email from Nora about dinner?",
            "gmail_search",
            "Dinner at 7",
        ),
    ],
    "device": [
        ("Can you check whether my desk lamp is switched on?", "home_status", "on"),
        ("See if the desk lamp is still on.", "home_status", "on"),
        ("Go ahead and switch it off.", "home_control", "off"),
        ("Is it off now?", "home_status", "off"),
        ("Isn't the desk lamp on?", "home_status", "off"),
        ("Please check whether the desk lamp isn't on.", "home_status", "off"),
        ("Could you check if the desk lamp is not on?", "home_status", "off"),
        ("Is the desk lamp not on?", "home_status", "off"),
        ("Please check if my desk lamp is currently not on.", "home_status", "off"),
        ("Is my desk lamp currently not on?", "home_status", "off"),
        ("Could you please check whether my desk lamp is off?", "home_status", "off"),
        ("Can you please see if my desk lamp is on?", "home_status", "off"),
    ],
    "capability_summary": [
        (
            "What can you genuinely help me with from here? Just the useful version.",
            None,
            "genuinely useful",
        ),
    ],
}

# Opt-in real execution, limited to local inspection and public weather.
LIVE_FLOWS = {
    "weather_followup": FLOWS["weather_followup"],
    "weather_chain": FLOWS["weather_chain"],
    "local_inspection": [
        ("How much RAM do you have left?", "ram_usage", "Available"),
        (
            "Could you take a look at how Curie is doing?",
            "dashboard_snapshot",
            "Curie dashboard",
        ),
        ("Show me how much memory you're using.", "ram_usage", "RAM"),
        ("Please check Curie's health.", "dashboard_snapshot", "Curie dashboard"),
        (
            "Check if Curie is running properly.",
            "dashboard_snapshot",
            "Curie dashboard",
        ),
        ("Can you show me the computer specs?", "hardware", "Hardware acceleration"),
    ],
    "public_weather": [
        (
            "Could you check whether I need an umbrella in Hong Kong tomorrow?",
            "weather",
            "Hong Kong",
        ),
    ],
}


def behavioral_checks(row):
    expected = row["expected_capability"]
    calls = [item["capability"] for item in row["tool_calls"]]
    answer = str(row.get("assistant") or "")
    checks = {
        "has_answer": bool(answer.strip()) and not answer.startswith("[Error"),
        "selected_expected_capability": calls == ([expected] if expected else []),
        "contains_expected_result": row["expected_marker"].casefold()
        in answer.casefold(),
    }
    if expected:
        checks["execution_completed"] = row.get("execution_status") in {
            "completed",
            "verified",
        }
    if row["flow"] == "weather_followup" and row["turn"] == 2:
        checks["corrected_location"] = (
            "kyoto" in str(row["tool_calls"][0].get("parameters", {})).casefold()
        )
        checks["preserved_forecast_day"] = (
            "tomorrow" in str(row["tool_calls"][0].get("parameters", {})).casefold()
        )
    if row["flow"] == "weather_chain":
        params = row["tool_calls"][0].get("parameters", {}) if row["tool_calls"] else {}
        checks["preserved_forecast_day"] = "tomorrow" in str(params).casefold()
        checks["answer_still_about_tomorrow"] = "tomorrow" in answer.casefold()
    if row["flow"] == "device" and expected == "home_control":
        params = row["tool_calls"][0].get("parameters", {})
        checks["resolved_device_reference"] = (
            str(params.get("target") or "").casefold() == "desk lamp"
            and params.get("state") == "off"
        )
    if row["flow"] == "device" and expected == "home_status":
        params = row["tool_calls"][0].get("parameters", {}) if row["tool_calls"] else {}
        checks["exact_device_target"] = (
            params.get("target", "").casefold() == "desk lamp"
        )
    if expected == "calendar_list":
        params = row["tool_calls"][0]["parameters"] if row["tool_calls"] else {}
        checks["owner_timezone_used"] = all(
            str(params.get(key, "")).endswith("+08:00")
            for key in ("time_min", "time_max")
        )
    if row["flow"] == "calendar_draft":
        checks["draft_uses_owner_timezone"] = "+08:00" in answer
        checks["draft_requires_approval"] = (
            row.get("routing", {}).get("approval_required") is True
        )
    return checks


async def run_probe(
    report, live_read_only=False, model_routing=False, unavailable_accounts=False
):
    from agent.chat_workflow import ChatWorkflow
    from agent.tooling import get_runtime_registry
    from agent.tooling.contracts import CapabilityDefinition, ToolResult
    from memory import local_store
    from memory.session_store import get_session_manager

    report_path = Path(report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    registry = get_runtime_registry()
    flows = LIVE_FLOWS if live_read_only else FLOWS
    if model_routing:
        from evaluation.model_intent_probe import CASES

        markers = {
            "ram_usage": "RAM",
            "hardware": "Hardware acceleration",
            "home_status": "on",
            "gmail_search": "Dinner at 7",
            "dashboard_snapshot": "healthy",
        }
        flows = {
            f"model_{index}": [(text, name, markers[name])]
            for index, (text, name) in enumerate(CASES)
            if not unavailable_accounts or name == "gmail_search"
        }
    real_execute = registry.execute
    calls = []
    state = {"desk lamp": "on"}
    sandbox_capabilities = {
        expected for turns in flows.values() for _, expected, _ in turns if expected
    }
    sandbox_capabilities.add("calendar_create")

    async def external_boundary(name, params, context):
        if unavailable_accounts:
            return await real_execute(name, params, context)
        calls.append({"capability": name, "parameters": dict(params)})
        if model_routing and name in {"ram_usage", "hardware"}:
            return await real_execute(name, params, context)
        if live_read_only:
            if name not in {"ram_usage", "dashboard_snapshot", "weather", "hardware"}:
                raise RuntimeError(f"Live probe blocked unexpected capability: {name}")
            try:
                return await real_execute(name, params, context)
            except Exception as exc:
                calls[-1]["failure"] = {
                    "type": type(exc).__name__,
                    "code": getattr(exc, "error_code", None),
                    "http_status": getattr(exc, "http_status", None),
                }
                raise
        if name == "calendar_list":
            return ToolResult(
                "Tomorrow: Dentist at 9:30 a.m. (Sandbox fixture)",
                {"events": [{"summary": "Dentist", "start": "09:30"}]},
                "sandbox calendar",
            )
        if name == "weather":
            # Exercise the real weather parser and formatter. Only the provider
            # boundary is synthetic; never substitute a canned tool response.
            from agent.tooling.weather_tool import WeatherTool

            async def provider(city, start, end, unit="metric"):
                calls[-1]["provider_request"] = {
                    "city": city,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                }
                return {
                    "city": city,
                    "source": "sandbox weather provider",
                    "days": [
                        {
                            "date": start.isoformat(),
                            "precipitation_probability": 80,
                            "rain_mm": 2,
                        }
                    ],
                }

            with patch("utils.weather.get_weather_range", side_effect=provider):
                return await WeatherTool().execute(params, context)
        if name == "observatory_project_analytics":
            return ToolResult(
                "Atmosphere recorded 128 page views in the last 30 days. (Sandbox fixture)",
                {"project": "Atmosphere", "views": 128, "days": 30},
                "sandbox analytics",
            )
        if name == "dashboard_snapshot":
            return ToolResult(
                "Curie is healthy and all core services are online. (Sandbox fixture)",
                {"status": "healthy", "services_online": True},
                "sandbox dashboard",
            )
        if name == "places_search":
            return ToolResult(
                "Central Coffee is open now. (Sandbox fixture)",
                {"places": [{"name": "Central Coffee", "open_now": True}]},
                "sandbox places",
            )
        if name == "flight_track":
            return ToolResult(
                "CX101 is en route and on time. (Sandbox fixture)",
                {"flight": "CX101", "status": "en route"},
                "sandbox flight tracker",
            )
        if name == "research":
            return ToolResult(
                "Latest local AI news: a new edge-inference benchmark was published. (Sandbox fixture)",
                {"items": [{"title": "Local AI benchmark"}]},
                "sandbox public web",
            )
        if name == "gmail_search":
            return ToolResult(
                "Nora — Dinner at 7. (Sandbox fixture)",
                {"messages": [{"sender": "Nora", "subject": "Dinner at 7"}]},
                "sandbox Gmail",
            )
        if name in {"home_status", "home_control"}:
            if name == "home_control":
                state["desk lamp"] = str(params.get("state") or "off")
            action = "Turned" if name == "home_control" else "Checked"
            value = state["desk lamp"]
            return ToolResult(
                f"{action} Desk Lamp: {value}. (Sandbox fixture)",
                {
                    "verification_status": "verified",
                    "verified": True,
                    "devices": [{"name": "Desk Lamp", "state": value}],
                },
                "sandbox device",
            )
        raise PermissionError("Capability is outside this sandboxed evaluation")

    results = []
    run_id = uuid.uuid4().hex
    with ExitStack() as stack:
        if unavailable_accounts:

            async def blocked_executor(params, context):
                calls.append(
                    {"capability": "blocked_unexpected_execution", "parameters": {}}
                )
                raise PermissionError(
                    "All executors are blocked in unavailable-account probes"
                )

            for definition in registry.all():
                stack.enter_context(
                    patch.object(
                        definition.executor, "execute", side_effect=blocked_executor
                    )
                )
        if not live_read_only:
            stack.enter_context(
                patch.object(
                    CapabilityDefinition,
                    "availability",
                    new=lambda item: (
                        item.name in sandbox_capabilities
                        and not (unavailable_accounts and item.name == "gmail_search"),
                        "sandboxed capability-awareness fixture",
                    ),
                )
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
        for flow, turns in flows.items():
            owner = f"capability-evaluation-{run_id}-{flow}"
            workflow = ChatWorkflow(max_history=10)
            workflow.learning_service.submit = lambda *args, **kwargs: None
            for index, (text, expected, marker) in enumerate(turns, 1):
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
                    "turn": index,
                    "user": text,
                    "assistant": result.get("text"),
                    "model": result.get("model_used"),
                    "seconds": round(time.monotonic() - started, 2),
                    "execution_status": result.get("execution_status"),
                    "routing": result.get("routing"),
                    "tool_calls": calls[before:],
                    "expected_capability": expected,
                    "expected_marker": marker,
                }
                if unavailable_accounts:
                    row["behavioral_checks"] = {
                        "no_execution": row["tool_calls"] == [],
                        "correct_capability": (row.get("routing") or {}).get(
                            "selected_capability"
                        )
                        == expected,
                        "exact_query": (row.get("routing") or {}).get("parameters")
                        == {"query": "dinner"},
                        "failed_status": row["execution_status"] == "failed",
                        "honest_unavailable": "unavailable"
                        in str(row["assistant"]).casefold(),
                        "no_fabricated_result": "Dinner at 7"
                        not in str(row["assistant"]),
                    }
                else:
                    row["behavioral_checks"] = behavioral_checks(row)
                if model_routing:
                    row["behavioral_checks"]["selected_by_model"] = (
                        row.get("routing") or {}
                    ).get("source") == "model"
                    if expected == "home_status":
                        row["behavioral_checks"]["exact_target"] = bool(
                            row["tool_calls"]
                            and row["tool_calls"][0]["parameters"]
                            .get("target", "")
                            .casefold()
                            == "desk lamp"
                        )
                    if expected == "gmail_search" and not unavailable_accounts:
                        row["behavioral_checks"]["preserved_query"] = bool(
                            row["tool_calls"]
                            and "dinner"
                            in row["tool_calls"][0]["parameters"]
                            .get("query", "")
                            .casefold()
                        )
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                report_path.write_text(
                    json.dumps({"complete": False, "transcript": results}, indent=2)
                )
                if not all(row["behavioral_checks"].values()):
                    raise RuntimeError(f"Capability gate failed: {flow} turn {index}")
            get_session_manager().reset_session("telegram", owner)

    checks = [value for row in results for value in row["behavioral_checks"].values()]
    report_path.write_text(
        json.dumps(
            {
                "complete": True,
                "execution_mode": (
                    "model_routing_unavailable_accounts"
                    if unavailable_accounts
                    else (
                        "model_routing_with_sandbox_accounts"
                        if model_routing
                        else "live_read_only" if live_read_only else "sandbox_fixtures"
                    )
                ),
                "turns": len(results),
                "checks_passed": sum(checks),
                "checks_total": len(checks),
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
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--model-routing", action="store_true")
    parser.add_argument("--unavailable-accounts", action="store_true")
    args = parser.parse_args()
    if args.unavailable_accounts and (not args.model_routing or args.live_read_only):
        parser.error(
            "--unavailable-accounts requires --model-routing without --live-read-only"
        )
    from dotenv import load_dotenv

    load_dotenv()
    with tempfile.TemporaryDirectory(prefix="curie-capability-probe-") as folder:
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
                "INTENT_LLM_ENABLED": "true" if args.model_routing else "false",
            }
        )
        from memory import local_store

        local_store._PATH = Path(folder) / "memory.sqlite3"
        asyncio.run(
            run_probe(
                args.report,
                args.live_read_only,
                args.model_routing,
                args.unavailable_accounts,
            )
        )


if __name__ == "__main__":
    main()
