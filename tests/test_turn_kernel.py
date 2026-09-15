import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.intent_router import classify_request
from agent.kernel.contracts import ResponseMode
from agent.kernel.dialogue_state import DialogueStateStore
from agent.kernel.understanding import analyze_turn
from agent.observability import TurnEventWriter
from agent.response_planner import select_response_mode
from agent.chat_workflow import ChatWorkflow
from agent.orchestration.contracts import ResponseCandidate
from connectors.telegram import WORKFLOW_COMMANDS


def test_operational_turn_has_typed_goal_and_skips_memory():
    analysis = analyze_turn(
        "Turn off the DreamView",
        "Turn off the DreamView",
        owner_id="",
        platform="telegram",
    )

    assert analysis.state.goal.intent == "capability"
    assert analysis.state.goal.subgoals[0].capability == "home_control"
    assert analysis.state.memory_policy == "operational_minimal"
    assert analysis.state.response_mode is ResponseMode.COMMAND_ACK
    assert analysis.state.entities[0].resolved_name == "DreamView"
    assert analysis.operational_decisions[0].parameters["state"] == "off"


def test_dialogue_state_resolves_device_pronouns_without_rewriting_normal_chat():
    store = DialogueStateStore()
    first = analyze_turn(
        "Turn off the DreamView",
        "Turn off the DreamView",
        owner_id="owner",
        platform="telegram",
    )
    store.observe_for_owner("owner", first.state, {"text": "Done."})

    resolved = store.resolve_references(
        "Turn it on", platform="telegram", owner_id="owner"
    )
    untouched = store.resolve_references(
        "I liked it", platform="telegram", owner_id="owner"
    )

    assert resolved.resolved_text == "Turn DreamView on"
    assert resolved.used_context is True
    assert untouched.resolved_text == "I liked it"
    assert untouched.used_context is False


def test_dialogue_state_keeps_recent_device_across_a_brief_social_interlude():
    store = DialogueStateStore()
    device_turn = analyze_turn(
        "Turn off DreamView",
        "Turn off DreamView",
        owner_id="owner",
        platform="telegram",
    )
    store.observe_for_owner("owner", device_turn.state, {"text": "Done."})
    social_turn = analyze_turn(
        "Thanks",
        "Thanks",
        owner_id="owner",
        platform="telegram",
    )
    store.observe_for_owner("owner", social_turn.state, {"text": "Anytime."})

    resolved = store.resolve_references(
        "Turn it on", platform="telegram", owner_id="owner"
    )
    assert resolved.resolved_text == "Turn DreamView on"


def test_compound_alias_and_control_becomes_an_ordered_plan():
    analysis = analyze_turn(
        "Correlate DreamView with AI Sync Box strip and turn it off",
        "Correlate DreamView with AI Sync Box strip and turn it off",
        owner_id="",
        platform="telegram",
    )

    assert [item.selected_capability for item in analysis.operational_decisions] == [
        "home_alias",
        "home_control",
    ]
    assert analysis.operational_decisions[0].parameters == {
        "device": "AI Sync Box strip",
        "alias": "DreamView",
    }
    assert analysis.operational_decisions[1].parameters["target"] == "AI Sync Box strip"
    assert analysis.state.goal.subgoals[1].depends_on == (
        analysis.state.goal.subgoals[0].id,
    )


def test_opposite_state_report_retries_the_active_device_goal():
    request = classify_request(
        "DreamView is still on",
        history=[("user", "Turn off the DreamView"), ("assistant", "Done.")],
    )

    assert request.action == "home_control"
    assert request.params["target"] == "DreamView"
    assert request.params["state"] == "off"


def test_generic_device_status_wording_is_understood():
    request = classify_request("Is the DreamView on?")
    assert request.action == "home_status"
    assert request.params["target"] == "DreamView"


def test_response_modes_are_explicit_and_contextual():
    assert (
        select_response_mode(
            "Turn it off", routing_intent="capability", capability="home_control"
        )
        is ResponseMode.COMMAND_ACK
    )
    assert (
        select_response_mode(
            "Is it on?", routing_intent="capability", capability="home_status"
        )
        is ResponseMode.STATUS
    )
    assert (
        select_response_mode("Which device?", routing_intent="clarification")
        is ResponseMode.CLARIFICATION
    )
    assert (
        select_response_mode("Explain the architecture in depth") is ResponseMode.DEEP
    )


def test_turn_event_trace_contains_metadata_but_no_message_or_response(tmp_path):
    writer = TurnEventWriter(tmp_path / "events.jsonl")
    analysis = analyze_turn(
        "Turn off secret DreamView",
        "Turn off secret DreamView",
        owner_id="private-owner",
        platform="telegram",
    )
    writer.record(
        analysis.state,
        {
            "text": "Done. Secret DreamView is now off.",
            "model_used": "action_router:home_control",
            "processing_time_ms": 12,
            "timings_ms": {"tool": 10},
        },
    )

    raw = (tmp_path / "events.jsonl").read_text()
    event = json.loads(raw)
    assert "secret" not in raw.casefold()
    assert "private-owner" not in raw
    assert event["capabilities"] == ["home_control"]
    assert event["response_mode"] == "command_ack"
    assert event["response_chars"] > 0


def test_chat_operational_fast_path_never_loads_memory_or_calls_the_model():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow.routing_service.execute = AsyncMock(
        return_value=ResponseCandidate(
            "Done. DreamView is now off.", "action_router:home_control"
        )
    )
    workflow._batch_load_context = AsyncMock(
        side_effect=AssertionError("operational command loaded conversational memory")
    )
    workflow.model_service.generate = AsyncMock(
        side_effect=AssertionError("operational command called the model")
    )
    session = MagicMock()
    session.get_history.return_value = []
    normalized = {
        "platform": "telegram",
        "external_user_id": "55",
        "external_chat_id": "300",
        "message_id": "week12-fast-path",
        "text": "Turn off the DreamView",
        "internal_id": "owner",
    }

    with (
        patch("agent.chat_workflow.get_session_manager", return_value=session),
        patch("agent.chat_workflow.UserManager.update_user_profile"),
        patch("agent.chat_workflow.UserManager.get_user_profile", return_value={}),
        patch("agent.chat_workflow.turn_event_writer.record"),
    ):
        result = asyncio.run(workflow.process_message(normalized))

    assert result["text"] == "Done. DreamView is now off."
    assert result["routing"]["selected_capability"] == "home_control"
    assert result["turn_state"]["memory_policy"] == "operational_minimal"
    workflow._batch_load_context.assert_not_awaited()
    workflow.model_service.generate.assert_not_awaited()


def test_telegram_registers_all_workflow_owned_operational_commands():
    assert {
        "home",
        "gmail",
        "x",
        "browser",
        "memory",
        "skill",
        "approve",
        "reject",
        "status",
        "metrics",
        "doctor",
        "logs",
        "stop",
        "restart",
    } <= set(WORKFLOW_COMMANDS)
