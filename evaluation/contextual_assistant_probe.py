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
import re
from pathlib import Path
import tempfile
import time
import uuid
from unittest.mock import patch

FLOWS = {
    "evening_close": [
        "Let's leave our tiny dragon Pip guarding her bottle caps tonight.",
        "Thanks. Goodnight.",
    ],
    "screenshot_replay": [
        "Hey curie you already shared letta agents with me",
        "Thanks for the weather update",
    ],
    "everyday_chat": [
        "Hey Curie, I had a long day. Keep me company for a bit.",
        "No fixing anything tonight. What's a small, silly thing worth talking about?",
        "Ask me one question, not a whole interview.",
        "I'd choose a tiny dragon that hoards bottle caps.",
        "What would you call it?",
        "Let's call her Pip. She is green, very dramatic, and scared of butterflies.",
        "Give me a two-sentence scene with Pip.",
        "Keep her scared of butterflies. Don't turn her brave yet. Give me another two sentences.",
        "Switching topics: tomorrow I want a quiet morning. Suggest one simple thing, no schedule.",
        "Thanks. Goodnight.",
    ],
    "context_recall": [
        "Do we know whether the puzzle is finished?",
        "Tea or coffee? Pick one, with a brief reason.",
    ],
    "briefing_feedback": [
        "Hey Curie, you already shared Letta agents with me.",
        "That's not what I meant. The engineering update is repetitive, not the weather.",
        "Thanks for understanding. Anyway, tell me something amusing about octopuses.",
        "Why would they do that?",
    ],
    "natural_conversation": [
        "I haven't fixed the bug yet.",
        "I fixed the bug, but now the app crashes.",
        "That was not what I asked. I'm just venting, not asking for debugging advice.",
        "New topic: I found the missing puzzle piece under the sofa.",
        "Do we know whether the puzzle is finished?",
        "Tea and a book or another puzzle tonight? Pick one and tell me why.",
        "I prefer tea to coffee. Ask me one question about it.",
        "Mostly oolong. I like the roasted flavour.",
    ],
    "natural_holdout": [
        "I nearly solved the login issue. Nearly is doing a lot of work there.",
        "No advice yet, just give me a little sympathy and a dry joke.",
        "Enough about computers. For our fictional picnic, five people are coming, the budget is 75 dollars and Sam can't eat peanuts.",
        "Correction: six people, same budget. What is the budget per person now?",
        "Give me a short name for a fictional spaceship.",
        "Let's call it Moth instead.",
        "Back to the picnic, what food must we avoid?",
        "And what did we call the spaceship?",
    ],
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

SEED_HISTORY = {
    "screenshot_replay": [
        ("assistant", "Morning weather: Today 19–27°C, drizzle; Monday 21–25°C, rain."),
        (
            "assistant",
            "Engineering brief: Letta agents release, subagent handling and saved monitors.",
        ),
    ],
    "context_recall": [
        ("user", "I found the missing puzzle piece under the sofa."),
        ("assistant", "Voilà. The sofa was keeping a small souvenir."),
    ],
    "briefing_feedback": [
        (
            "assistant",
            "Morning weather: Today 19–27°C, drizzle; Monday 21–25°C, rain. Carry an umbrella.",
        ),
        (
            "assistant",
            "Engineering brief: Letta agents release. Improvements to subagent handling and saved monitors.",
        ),
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
    reply = str(row.get("assistant") or "").replace("’", "'")
    checks = {
        "has_answer": bool(reply.strip()) and not reply.startswith("[Error"),
        "only_expected_tool_calls": [c["capability"] for c in row["tool_calls"]]
        == expected_calls.get(key, []),
    }
    if key not in expected_calls:
        checks["no_unrequested_tool_route"] = not (
            (row.get("routing") or {}).get("selected_capability")
            or (
                row.get("execution_status") in {"failed", "completed", "queued"}
                and (row.get("routing") or {}).get("intent") != "social"
            )
        )
    if row["flow"] in {
        "briefing_feedback",
        "natural_conversation",
        "natural_holdout",
        "everyday_chat",
    }:
        checks["no_empty_service_closing"] = not re.search(
            r"(?:anything else|how can i (?:help|assist))", reply, re.I
        )
    if key in {
        ("natural_conversation", 1),
        ("natural_conversation", 2),
        ("natural_conversation", 3),
        ("natural_holdout", 1),
    }:
        checks["no_false_congratulations"] = not re.search(
            r"nicely done|a tidy result|congratulations|well done|you(?:'ve| have) (?:fixed|solved)",
            reply,
            re.I,
        )
    if row["flow"] in {"briefing_feedback", "screenshot_replay"}:
        checks["no_unrequested_forecast"] = not re.search(
            r"°[CF]|rain chance|weather outlook", reply, re.I
        )
    if key in {("briefing_feedback", 1), ("briefing_feedback", 2)}:
        checks["does_not_reissue_old_news"] = not re.search(
            r"latest news|subagent handling|saved monitors|only one available",
            reply,
            re.I,
        )
        checks["does_not_invent_internal_cause"] = not re.search(
            r"must have|conflated|notification|cache glitch|mixed up the timeline",
            reply,
            re.I,
        )
        checks["does_not_invent_release_status"] = not re.search(
            r"no release|still in development|November \d+", reply, re.I
        )
    if key in {("natural_conversation", 5), ("context_recall", 1)}:
        checks["grounded_uncertainty"] = bool(
            re.search(
                r"(?:don't|not|unknown|haven't|can't|cannot|unclear)", reply, re.I
            )
        )
        checks["uses_partial_progress_context"] = bool(
            re.search(r"found|finding|recover|mentioned|told|said", reply, re.I)
        )
        checks["does_not_send_owner_to_find_recovered_piece"] = not re.search(
            r"look under|check under|find the (?:missing )?piece", reply, re.I
        )
    if key == ("natural_conversation", 4):
        checks["does_not_claim_completion"] = not re.search(
            r"(?:your|the) puzzle is (?:complete|finished)|you(?:'ve| have) finished",
            reply,
            re.I,
        )
    if key == ("natural_conversation", 7):
        checks["one_invited_question"] = reply.count("?") == 1
    if key == ("natural_holdout", 4):
        checks["corrected_budget"] = "12.50" in reply or "12.5" in reply
    if key == ("natural_holdout", 3):
        checks["no_wrong_initial_budget"] = not re.search(
            r"per (?:person|head)|a head|each", reply, re.I
        ) or bool(re.search(r"\b15\b|fifteen", reply, re.I))
    if key == ("natural_holdout", 7):
        checks["retains_food_constraint"] = "peanut" in reply.casefold()
        checks["does_not_invent_additional_restrictions"] = not re.search(
            r"tree nuts|street foods|messy dips", reply, re.I
        )
    if key == ("natural_holdout", 8):
        checks["retains_chosen_name"] = "moth" in reply.casefold()
    if key == ("everyday_chat", 3):
        checks["one_invited_question"] = reply.count("?") == 1
    if key in {("everyday_chat", 7), ("everyday_chat", 8)}:
        checks["retains_character_name"] = "pip" in reply.casefold()
        # Quoted speech followed by a lowercase attribution is part of the same
        # sentence ("Stop!" she said.), not a third sentence in a two-sentence scene.
        checks["two_sentence_scene"] = (
            len(re.findall(r"[.!?](?:[\"'”’])?(?=\s+(?:[A-Z]|[\"“])|\s*$)", reply)) == 2
        )
    if key == ("everyday_chat", 9):
        checks["no_schedule"] = not re.search(r"\d{1,2}:\d{2}|\d+\s*[ap]m", reply, re.I)
        checks["leaves_old_topic"] = not re.search(r"pip|dragon|butterfl", reply, re.I)
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
    if key == ("device_flow", 3):
        checks["does_not_deny_verified_action"] = not any(
            phrase in reply.casefold()
            for phrase in (
                "didn't turn",
                "did not turn",
                "no record",
                "cannot control",
                "can't control",
            )
        )
        checks["attributes_owner_request"] = bool(
            re.search(
                r"\byou\s+(?:(?:explicitly|specifically|directly|originally)\s+)?(?:asked|told)\b|\byour request\b",
                reply,
                re.I,
            )
        )
    return checks


def configured_curie_persona():
    """Never silently evaluate ChatWorkflow's unrelated fallback persona."""
    from agent.persona_contract import active_persona

    persona = active_persona()
    if str(persona.get("name", "")).casefold() != "curie":
        raise RuntimeError("Curie probe requires the configured Curie persona")
    return persona


async def run_probe(report, *, keep_going=False):
    from agent.chat_workflow import ChatWorkflow
    from agent.tooling import get_runtime_registry
    from agent.tooling.contracts import CapabilityDefinition, ToolResult
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
    run_id = uuid.uuid4().hex
    persona = configured_curie_persona()
    with ExitStack() as stack:
        # Availability belongs to the fixture too; never borrow real credentials
        # merely to exercise the normal planner and execution boundary.
        sandbox_capabilities = {"x_search", "weather", "home_control", "home_status"}
        stack.enter_context(
            patch.object(
                CapabilityDefinition,
                "availability",
                new=lambda item: (
                    item.name in sandbox_capabilities,
                    "sandboxed evaluation capability",
                ),
            )
        )
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
        stack.enter_context(
            patch("services.research_followup.needs_research", return_value=False)
        )
        stack.enter_context(patch("agent.chat_workflow._TASK_TRACKING", False))
        stack.enter_context(
            patch("services.x_research.research_context", return_value="")
        )
        stack.enter_context(patch("services.knowledge_library.recall", return_value=[]))
        stack.enter_context(
            patch(
                "services.current_awareness.awareness_context",
                side_effect=lambda owner, *a, **k: (
                    {
                        "status": "ready",
                        "weather": "Today 19–27°C, drizzle. Monday 21–25°C, rain.",
                        "news": [],
                        "source": "synthetic background fixture",
                    }
                    if owner.endswith("briefing_feedback")
                    else None
                ),
            )
        )
        for flow, turns in FLOWS.items():
            owner = "evaluation-" + run_id + "-" + flow
            workflow = ChatWorkflow(
                persona=persona,
                max_history=max(5, int(os.getenv("CHAT_MAX_HISTORY", "25"))),
                enable_small_talk=False,
                minimal_sanitization=os.getenv("MINIMAL_SANITIZATION", "true").lower()
                == "true",
            )
            workflow.learning_service.submit = lambda *a, **k: None
            for role, content in SEED_HISTORY.get(flow, []):
                get_session_manager().add_message("telegram", owner, role, content)
            observed = []
            observed_routes = []
            generate = workflow.model_service.generate
            decide = workflow.routing_service.decide

            async def observe_routing(*args, **kwargs):
                decision = await decide(*args, **kwargs)
                observed_routes.append(decision.as_dict())
                return decision

            workflow.routing_service.decide = observe_routing

            async def observe_generation(prompt, user_text, *args, **kwargs):
                candidate = await generate(prompt, user_text, *args, **kwargs)
                observed.append(
                    {
                        "raw_reply": candidate.text,
                        "capability_scope": kwargs.get("capability_scope", "full"),
                        "last_prompt_message": getattr(prompt, "messages", [{}])[-1],
                        "prompt_history": getattr(prompt, "messages", [])[1:-1],
                        "system_words": sum(
                            len(m["content"].split())
                            for m in getattr(prompt, "messages", [])
                            if m["role"] == "system"
                        ),
                    }
                )
                return candidate

            workflow.model_service.generate = observe_generation
            for index, text in enumerate(turns):
                before = len(calls)
                observed.clear()
                observed_routes.clear()
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
                    "persona": workflow.persona["name"],
                    "history_limit": workflow.max_history,
                    "flow": flow,
                    "turn": index + 1,
                    "user": text,
                    "assistant": result.get("text"),
                    "model": result.get("model_used"),
                    "seconds": round(time.monotonic() - started, 2),
                    "execution_status": result.get("execution_status"),
                    "routing": result.get("routing"),
                    "observed_routing": list(observed_routes),
                    "coordination": result.get("coordination"),
                    "tool_calls": calls[before:],
                    "generation": list(observed),
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
                if not keep_going and not all(row["behavioral_checks"].values()):
                    raise RuntimeError(
                        f"Behavioral gate failed: {flow} turn {index + 1}"
                    )
            get_session_manager().reset_session("telegram", owner)
    Path(report).write_text(
        json.dumps(
            {
                "tool_mode": "sandboxed_external_boundary",
                "complete": True,
                "behavioral_gates_passed": all(
                    all(row["behavioral_checks"].values()) for row in results
                ),
                "transcript": results,
                "temporary_database": str(local_store._PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if any(not all(row["behavioral_checks"].values()) for row in results):
        raise RuntimeError("Behavioral gates failed; see the complete probe report")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--flow", choices=FLOWS)
    parser.add_argument("--keep-going", action="store_true")
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
                "ASSISTANT_STATE_DIR": folder + "/state",
                "CURIE_PIPELINE_ROLLOUT_STATE": folder + "/rollout.json",
                "CURIE_TURN_TRACE_PATH": folder + "/turns.jsonl",
                "CURIE_CREDENTIAL_STORE": folder + "/credentials.enc",
                "CURIE_RESOURCE_BUDGET_PATH": folder + "/budgets.json",
                "CURIE_DIAGNOSTICS_PATH": folder + "/diagnostics.json",
                "LLM_PROVIDER_PRIORITY": "llama.cpp",
                "CURIE_REMOTE_MODELS_ENABLED": "false",
                "LLM_ALTERNATE_ENABLED": "false",
                "CURIE_INFERENCE_SOCKET": str(
                    Path(__file__).resolve().parents[1] / "log/inference/worker.sock"
                ),
                "POSTGRES_HOST": "",
                "MONGODB_URI": "",
                "INTENT_LLM_ENABLED": "false",
            }
        )
        for role in (
            "GENERAL",
            "FAST",
            "REASONING",
            "DEEP_REASONING",
            "CODING",
            "CRITIC",
            "INTENT",
        ):
            os.environ[f"LLM_{role}_PROVIDER"] = "local"
        from memory import local_store

        local_store._PATH = Path(folder) / "memory.sqlite3"
        from agent.observability import reset_telemetry

        reset_telemetry(path=Path(folder) / "telemetry.json")
        asyncio.run(run_probe(args.report, keep_going=args.keep_going))


if __name__ == "__main__":
    main()
