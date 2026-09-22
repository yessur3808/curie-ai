import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.kernel.feature_flags import (
    PIPELINE_CONTROL_TOKEN,
    PipelineFeatureFlags,
    PipelineMode,
)
from agent.kernel.pipeline import (
    PIPELINE_STAGE_ORDER,
    PipelineStage,
    SideEffect,
    StageOutput,
    StageStatus,
    TurnPipeline,
    thaw,
)
from agent.orchestration.contracts import ResponseCandidate


def _latest_artifact(state):
    for stage in reversed(PIPELINE_STAGE_ORDER):
        value = state.artifact(stage)
        if value is not None:
            return thaw(value)
    return thaw(state.seed)


def _passing_handlers():
    handlers = {}
    for stage in PIPELINE_STAGE_ORDER:

        async def handler(state, current=stage):
            artifact = _latest_artifact(state)
            if current is PipelineStage.NORMALIZE:
                artifact = thaw(state.seed)
            if current is PipelineStage.RESOLVE_IDENTITY:
                artifact["internal_id"] = "owner"
            if current is PipelineStage.RENDER_RESPONSE:
                artifact.setdefault("text", "Ready.")
            return StageOutput(
                artifact,
                {"stage_completed": current.value},
            )

        handlers[stage] = handler
    return handlers


@pytest.mark.asyncio
async def test_pipeline_runs_every_stage_in_fixed_order_with_shared_trace_ids():
    state = await TurnPipeline(_passing_handlers()).run(
        {
            "platform": "telegram",
            "external_user_id": "1",
            "external_chat_id": "2",
            "text": "Hello Curie",
        }
    )

    assert tuple(item.stage for item in state.stage_results) == PIPELINE_STAGE_ORDER
    assert all(item.status is StageStatus.COMPLETED for item in state.stage_results)
    assert {item.trace_id for item in state.stage_results} == {state.trace_id}
    assert {item.turn_id for item in state.stage_results} == {state.turn_id}


@pytest.mark.asyncio
async def test_context_failure_degrades_safely_and_later_stages_continue():
    handlers = _passing_handlers()

    async def fail_context(_state):
        raise RuntimeError("database contains private diagnostic detail")

    handlers[PipelineStage.BUILD_CONTEXT] = fail_context
    state = await TurnPipeline(handlers).run(
        {
            "platform": "telegram",
            "external_user_id": "1",
            "external_chat_id": "2",
            "text": "Hello",
        }
    )

    context = state.stage_results[
        PIPELINE_STAGE_ORDER.index(PipelineStage.BUILD_CONTEXT)
    ]
    assert context.status is StageStatus.DEGRADED
    assert context.error is not None
    assert context.error.exception_type == "RuntimeError"
    assert state.stage_results[-1].status is StageStatus.COMPLETED
    assert "private diagnostic detail" not in json.dumps(state.as_dict())


@pytest.mark.asyncio
async def test_cancellation_after_authorization_never_enters_mutating_execute():
    handlers = _passing_handlers()
    cancelled = asyncio.Event()
    mutation_called = False

    async def authorize(state):
        cancelled.set()
        return StageOutput(_latest_artifact(state))

    async def mutate(state):
        nonlocal mutation_called
        mutation_called = True
        return StageOutput(
            _latest_artifact(state),
            effects=frozenset({SideEffect.TOOL_MUTATION}),
        )

    handlers[PipelineStage.AUTHORIZE] = authorize
    handlers[PipelineStage.EXECUTE] = mutate
    state = await TurnPipeline(handlers).run(
        {
            "platform": "telegram",
            "external_user_id": "1",
            "external_chat_id": "2",
            "text": "Turn it off",
        },
        cancellation_event=cancelled,
    )

    execute = state.stage_results[PIPELINE_STAGE_ORDER.index(PipelineStage.EXECUTE)]
    assert mutation_called is False
    assert execute.status is StageStatus.CANCELLED
    assert all(
        item.status is StageStatus.SKIPPED
        for item in state.stage_results[
            PIPELINE_STAGE_ORDER.index(PipelineStage.EXECUTE) + 1 :
        ]
    )


@pytest.mark.asyncio
async def test_render_failure_preserves_verified_outcome_in_minimal_fallback():
    handlers = _passing_handlers()
    verified = {
        "text": "The lamp is off.",
        "verification_status": "verified",
    }

    async def verify(_state):
        return StageOutput(verified, {"verified": True})

    async def fail_render(_state):
        raise RuntimeError("template unavailable")

    def fallback(state):
        return StageOutput(
            thaw(state.artifact(PipelineStage.VERIFY)),
            {"fallback": True},
            status=StageStatus.DEGRADED,
        )

    handlers[PipelineStage.VERIFY] = verify
    handlers[PipelineStage.RENDER_RESPONSE] = fail_render
    state = await TurnPipeline(handlers, fallback_renderer=fallback).run(
        {
            "platform": "telegram",
            "external_user_id": "1",
            "external_chat_id": "2",
            "text": "Turn off the lamp",
        }
    )

    rendered = state.stage_results[
        PIPELINE_STAGE_ORDER.index(PipelineStage.RENDER_RESPONSE)
    ]
    final = thaw(state.artifact(PipelineStage.RECORD_LEARNING))
    assert rendered.status is StageStatus.DEGRADED
    assert rendered.error is not None
    assert final["text"] == "The lamp is off."
    assert final["verification_status"] == "verified"


@pytest.mark.asyncio
async def test_renderer_cannot_declare_or_initiate_tool_mutation():
    handlers = _passing_handlers()

    async def invalid_renderer(state):
        return StageOutput(
            _latest_artifact(state),
            effects=frozenset({SideEffect.TOOL_MUTATION}),
        )

    handlers[PipelineStage.RENDER_RESPONSE] = invalid_renderer
    state = await TurnPipeline(handlers).run(
        {
            "platform": "telegram",
            "external_user_id": "1",
            "external_chat_id": "2",
            "text": "Hello",
        }
    )

    rendered = state.stage_results[
        PIPELINE_STAGE_ORDER.index(PipelineStage.RENDER_RESPONSE)
    ]
    assert rendered.status is StageStatus.FAILED
    assert rendered.error is not None
    assert all(
        item.status is StageStatus.SKIPPED
        for item in state.stage_results[
            PIPELINE_STAGE_ORDER.index(PipelineStage.DELIVER) :
        ]
    )


@pytest.mark.asyncio
async def test_pipeline_serialization_excludes_inputs_artifacts_and_token_values():
    fixture_value = "redaction-fixture-" + ("x" * 32)
    handlers = _passing_handlers()

    async def normalize(state):
        artifact = thaw(state.seed)
        return StageOutput(
            artifact,
            {
                "api_key": fixture_value,
                "note": f"bearer {fixture_value}",
                "safe_count": 2,
            },
        )

    handlers[PipelineStage.NORMALIZE] = normalize
    state = await TurnPipeline(handlers).run(
        {
            "platform": "telegram",
            "external_user_id": "private-user",
            "external_chat_id": "private-chat",
            "text": f"do not log {fixture_value}",
            "access_token": fixture_value,
            "trace_id": fixture_value,
        }
    )

    serialized = json.dumps(state.as_dict(), sort_keys=True)
    assert fixture_value not in serialized
    assert "private-user" not in serialized
    assert "private-chat" not in serialized
    assert "[redacted]" in serialized
    assert "safe_count" in serialized


def test_feature_flags_support_default_connector_owner_and_explicit_rollout(
    monkeypatch,
):
    monkeypatch.setenv("CURIE_TURN_PIPELINE_MODE", "legacy")
    monkeypatch.setenv("CURIE_TURN_PIPELINE_ACTIVE_OWNERS", "owner-active")
    monkeypatch.setenv("CURIE_TURN_PIPELINE_SHADOW_CONNECTORS", "TeLeGrAm")
    flags = PipelineFeatureFlags.from_env()

    assert flags.mode_for({"internal_id": "owner-active"}) is PipelineMode.ACTIVE
    assert flags.mode_for({"platform": "telegram"}) is PipelineMode.SHADOW
    assert flags.mode_for({"platform": "api"}) is PipelineMode.LEGACY
    assert (
        flags.mode_for({"platform": "api", "_pipeline_mode": "active"})
        is PipelineMode.LEGACY
    )
    assert (
        flags.mode_for(
            {
                "platform": "api",
                "_pipeline_mode": "active",
                "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
            }
        )
        is PipelineMode.ACTIVE
    )


@pytest.mark.asyncio
async def test_active_chat_pipeline_completes_text_turn_without_changing_text():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow._process_message_core = AsyncMock(
        return_value={
            "text": "Hey! What are we working on?",
            "timestamp": datetime.now(timezone.utc),
            "model_used": "test-model",
            "processing_time_ms": 3.0,
        }
    )
    normalized = {
        "platform": "telegram",
        "external_user_id": "55",
        "external_chat_id": "300",
        "message_id": "phase1-conversation",
        "text": "Hey Curie",
        "internal_id": "owner",
        "_pipeline_mode": "active",
        "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
    }

    with (
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record"),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record_pipeline"),
    ):
        result = await workflow.process_message(normalized)

    assert result["text"] == "Hey! What are we working on?"
    assert result["response_origin"] == "turn_pipeline"
    assert [item["stage"] for item in result["pipeline"]["stages"]] == [
        stage.value for stage in PIPELINE_STAGE_ORDER
    ]
    assert result["pipeline"]["stages"][11]["status"] == "deferred"
    workflow._process_message_core.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_rollout_flag_resolves_missing_identity_before_path_selection():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow.pipeline_flags = PipelineFeatureFlags(
        active_owners=frozenset({"owner-active"})
    )
    workflow._process_message_core = AsyncMock(
        return_value={
            "text": "Ready.",
            "timestamp": datetime.now(timezone.utc),
            "model_used": "test-model",
            "processing_time_ms": 1.0,
        }
    )
    normalized = {
        "platform": "telegram",
        "external_user_id": "55",
        "external_chat_id": "300",
        "message_id": "phase1-owner-rollout",
        "text": "Hello",
    }

    with (
        patch(
            "agent.chat_workflow.UserManager.get_or_create_user_internal_id",
            return_value="owner-active",
        ) as resolve_owner,
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record"),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record_pipeline"),
    ):
        result = await workflow.process_message(normalized)

    assert result["response_origin"] == "turn_pipeline"
    assert result["pipeline"]["mode"] == "active"
    resolve_owner.assert_called_once()


@pytest.mark.asyncio
async def test_active_deterministic_device_command_bypasses_model_generation():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow.routing_service.execute = AsyncMock(
        return_value=ResponseCandidate(
            "Done. Both lights are off.",
            "action_router:home_control",
            {
                "status": "completed",
                "verification_status": "verified",
                "data": {
                    "receipts": [
                        {"name": "AI Sync Box strip"},
                        {"name": "Floor Lamp 2"},
                    ]
                },
            },
        )
    )
    workflow.model_service.generate = AsyncMock(
        side_effect=AssertionError("device command called the conversation model")
    )
    workflow._batch_load_context = AsyncMock(
        side_effect=AssertionError("device command loaded conversational memory")
    )
    session = MagicMock()
    session.get_history.return_value = []
    normalized = {
        "platform": "telegram",
        "external_user_id": "55",
        "external_chat_id": "300",
        "message_id": "phase1-device",
        "text": "Turn off all lights",
        "internal_id": "owner",
        "_pipeline_mode": "active",
        "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
    }

    with (
        patch("agent.chat_workflow.get_session_manager", return_value=session),
        patch("agent.chat_workflow.UserManager.update_user_profile"),
        patch("agent.chat_workflow.UserManager.get_user_profile", return_value={}),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record"),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record_pipeline"),
    ):
        result = await workflow.process_message(normalized)

    assert result["text"] == "Done. Both lights are off."
    assert result["verification_status"] == "verified"
    assert result["pipeline"]["failed_stage"] is None
    workflow.model_service.generate.assert_not_awaited()
    workflow._batch_load_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_device_alias_is_resolved_before_routing():
    from memory.self_learning import set_session_adaptation

    set_session_adaptation(
        "owner",
        "telegram",
        "device_reference",
        {"alias": "dreamview", "target": "AI Sync Box strip"},
    )
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    workflow.routing_service.execute = AsyncMock(
        return_value=ResponseCandidate(
            "Done. AI Sync Box strip is off.",
            "action_router:home_control",
            {
                "status": "completed",
                "verification_status": "verified",
                "data": {"receipts": [{"name": "AI Sync Box strip"}]},
            },
        )
    )
    normalized = {
        "platform": "telegram",
        "external_user_id": "55",
        "external_chat_id": "300",
        "message_id": "phase8-session-alias",
        "text": "Turn off dreamview",
        "internal_id": "owner",
        "_pipeline_mode": "active",
        "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
    }

    with (
        patch("agent.chat_workflow.UserManager.update_user_profile"),
        patch("agent.chat_workflow.UserManager.get_user_profile", return_value={}),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record"),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record_pipeline"),
    ):
        result = await workflow.process_message(normalized)

    assert result["text"] == "Done. AI Sync Box strip is off."
    assert any(
        call.args[1] == "Turn off AI Sync Box strip"
        for call in workflow.routing_service.execute.await_args_list
    )


@pytest.mark.asyncio
async def test_shadow_mode_keeps_legacy_response_authoritative_and_compares_routes():
    workflow = ChatWorkflow(persona={"name": "Curie", "system_prompt": "Be helpful."})
    legacy = {
        "text": "Done. DreamView is off.",
        "timestamp": datetime.now(timezone.utc),
        "model_used": "action_router:home_control",
        "processing_time_ms": 2.0,
        "routing": {"selected_capability": "home_control"},
    }
    workflow._process_message_legacy = AsyncMock(return_value=dict(legacy))
    normalized = {
        "platform": "telegram",
        "external_user_id": "55",
        "external_chat_id": "300",
        "message_id": "phase1-shadow",
        "text": "Turn off DreamView",
        "internal_id": "owner",
        "_pipeline_mode": "shadow",
        "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
    }

    with patch("agent.chat_workflow.turn_event_writer.record_pipeline"):
        result = await workflow.process_message(normalized)

    assert result["text"] == legacy["text"]
    assert result["response_origin"] == "legacy_chat_workflow"
    assert result["pipeline_shadow"]["mode"] == "shadow"
    assert result["pipeline_shadow_comparison"]["route_match"] is True
    workflow._process_message_legacy.assert_awaited_once()
