"""Legacy-compatible adapter for the typed Phase 1 turn pipeline."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import TYPE_CHECKING, Any, Mapping

from memory import UserManager
from memory.session_store import get_session_manager

from agent.kernel.dialogue_state import ReferenceResolution
from agent.kernel.feature_flags import PipelineMode
from agent.kernel.pipeline import (
    PipelineStage,
    PipelineState,
    SideEffect,
    StageOutput,
    StageStatus,
    TurnPipeline,
    thaw,
)
from agent.kernel.planning import ExecutionPlan, build_execution_plan
from agent.kernel.understanding import TurnAnalysis, analyze_turn
from agent.observability import turn_event_writer

if TYPE_CHECKING:
    from agent.chat_workflow import ChatWorkflow

_DEVICE_REFERENCE = re.compile(
    r"\b(?:it|that(?: one| device)?|this(?: one| device)?|them|"
    r"those(?: devices)?|these(?: devices)?|both(?: devices)?|the device)\b",
    re.I,
)
_DEVICE_OPERATION = re.compile(
    r"\b(?:turn|switch|power|status|still (?:on|off)|"
    r"(?:is|are)\b.{0,50}\b(?:on|off|online|offline|running))\b",
    re.I,
)


def _mapping(value: Any) -> dict[str, Any]:
    return thaw(value) if isinstance(value, Mapping) else {}


def _artifact(
    state: PipelineState, stage: PipelineStage, fallback: PipelineStage | None = None
) -> dict[str, Any]:
    value = state.artifact(stage)
    if value is None and fallback is not None:
        value = state.artifact(fallback)
    return _mapping(value)


class LegacyTurnPipelineAdapter:
    """Run the new stage contract while delegating behavior to proven services.

    Phase 1 intentionally keeps the existing core as one legacy execution bridge.
    Later phases replace individual bridge responsibilities without changing the
    public stage contract.
    """

    def __init__(self, workflow: "ChatWorkflow"):
        self.workflow = workflow

    def _handlers(
        self,
        *,
        shadow_result: Mapping[str, Any] | None = None,
    ) -> dict[PipelineStage, Any]:
        shadow = shadow_result is not None

        async def normalize(state: PipelineState) -> StageOutput:
            enriched = _mapping(state.seed)
            enriched["platform"] = str(enriched.get("platform") or "unknown")
            enriched["text"] = str(enriched.get("text") or "").strip()
            missing = [
                key
                for key in ("external_user_id", "external_chat_id", "text")
                if not enriched.get(key)
            ]
            if missing:
                raise ValueError("normalized input is missing required fields")
            return StageOutput(
                enriched,
                {
                    "platform": enriched["platform"],
                    "message_chars": len(enriched["text"]),
                    "required_fields_present": True,
                },
            )

        async def resolve_identity(state: PipelineState) -> StageOutput:
            enriched = _artifact(state, PipelineStage.NORMALIZE)
            internal_id = enriched.get("internal_id")
            effects: frozenset[SideEffect] = frozenset()
            if not internal_id:
                if shadow:
                    internal_id = "shadow-owner"
                else:
                    internal_id = UserManager.get_or_create_user_internal_id(
                        channel=str(enriched["platform"]),
                        external_id=str(enriched["external_user_id"]),
                        secret_username=(
                            f"{enriched['platform']}_{enriched['external_user_id']}"
                        ),
                        updated_by="turn_pipeline",
                    )
                    effects = frozenset({SideEffect.IDENTITY_WRITE})
                enriched["internal_id"] = internal_id
            return StageOutput(
                enriched,
                {"owner_resolved": True},
                effects,
            )

        async def build_context(state: PipelineState) -> StageOutput:
            enriched = _artifact(state, PipelineStage.RESOLVE_IDENTITY)
            original_text = str(enriched.get("text") or "")
            needs_history = bool(
                _DEVICE_REFERENCE.search(original_text)
                and _DEVICE_OPERATION.search(original_text)
            ) or bool(re.search(r"\btry again\b", original_text, re.I))
            history = []
            effects: frozenset[SideEffect] = frozenset()
            if needs_history:
                history = get_session_manager().get_history(
                    str(enriched["platform"]), str(enriched["internal_id"])
                )[-8:]
                effects = frozenset({SideEffect.STORAGE_READ})
            return StageOutput(
                {"enriched": enriched, "history": history},
                {
                    "reference_history_loaded": bool(history),
                    "history_items": len(history),
                },
                effects,
            )

        async def understand(state: PipelineState) -> StageOutput:
            context = _artifact(state, PipelineStage.BUILD_CONTEXT)
            enriched = _mapping(context.get("enriched")) or _artifact(
                state, PipelineStage.RESOLVE_IDENTITY
            )
            history = list(context.get("history") or ())
            original_text = str(enriched.get("text") or "")
            resolution: ReferenceResolution = (
                self.workflow.dialogue_state.resolve_references(
                    original_text,
                    platform=str(enriched["platform"]),
                    owner_id=str(enriched["internal_id"]),
                    history=history,
                )
            )
            analysis = analyze_turn(
                original_text,
                resolution.resolved_text,
                owner_id=str(enriched["internal_id"]),
                platform=str(enriched["platform"]),
                history=history,
                trace_id=state.trace_id,
                resolved_entities=resolution.entities,
            )
            enriched["_effective_text"] = resolution.resolved_text
            enriched["_turn_analysis"] = analysis
            return StageOutput(
                {"enriched": enriched, "history": history, "analysis": analysis},
                {
                    "intent": analysis.state.goal.intent,
                    "risk": analysis.state.goal.risk,
                    "entity_count": len(analysis.state.entities),
                    "used_reference_context": resolution.used_context,
                },
            )

        async def route(state: PipelineState) -> StageOutput:
            understood = _artifact(state, PipelineStage.UNDERSTAND)
            analysis = understood.get("analysis")
            if not isinstance(analysis, TurnAnalysis):
                raise RuntimeError("understanding did not produce TurnAnalysis")
            decisions = analysis.operational_decisions
            return StageOutput(
                understood,
                {
                    "route": "operational" if decisions else "legacy_conversation",
                    "decision_count": len(decisions),
                    "capabilities": [
                        decision.selected_capability
                        for decision in decisions
                        if decision.selected_capability
                    ],
                },
            )

        async def plan(state: PipelineState) -> StageOutput:
            routed = _artifact(state, PipelineStage.ROUTE)
            analysis = routed.get("analysis")
            if not isinstance(analysis, TurnAnalysis):
                raise RuntimeError("routing did not preserve TurnAnalysis")
            execution_plan: ExecutionPlan | None = None
            if analysis.operational_decisions:
                execution_plan = build_execution_plan(
                    analysis.state,
                    analysis.operational_decisions,
                    preserve_order=analysis.preserve_order,
                )
                enriched = _mapping(routed.get("enriched"))
                enriched["_execution_plan"] = execution_plan
                routed["enriched"] = enriched
            routed["execution_plan"] = execution_plan
            return StageOutput(
                routed,
                {
                    "planned": execution_plan is not None,
                    "step_count": len(execution_plan.steps) if execution_plan else 0,
                    "risk": execution_plan.risk if execution_plan else "none",
                },
            )

        async def authorize(state: PipelineState) -> StageOutput:
            planned = _artifact(state, PipelineStage.PLAN)
            execution_plan = planned.get("execution_plan")
            requires_approval = bool(
                isinstance(execution_plan, ExecutionPlan)
                and execution_plan.approval_strategy == "per_consequential_step"
            )
            # The existing registry executor remains the enforcement point in
            # Phase 1. This stage proves that enforcement occurs before execute.
            return StageOutput(
                planned,
                {
                    "approval_required": requires_approval,
                    "enforcement": (
                        "delegated_to_capability_policy"
                        if requires_approval
                        else "preauthorized_policy"
                    ),
                },
            )

        async def execute(state: PipelineState) -> StageOutput:
            authorized = _artifact(state, PipelineStage.AUTHORIZE)
            enriched = _mapping(authorized.get("enriched"))
            execution_plan = authorized.get("execution_plan")
            if shadow:
                result = dict(shadow_result or {})
                effects: frozenset[SideEffect] = frozenset()
            else:
                result = await self.workflow._process_message_core(enriched)
                declared = {
                    SideEffect.LEGACY_BRIDGE,
                    SideEffect.PERSISTENCE,
                    SideEffect.LEARNING,
                }
                if isinstance(execution_plan, ExecutionPlan):
                    declared.add(
                        SideEffect.TOOL_MUTATION
                        if execution_plan.risk == "mutating"
                        else SideEffect.TOOL_READ
                    )
                effects = frozenset(declared)
            return StageOutput(
                result,
                {
                    "result_present": bool(result),
                    "model_family": str(result.get("model_used") or "unknown").split(
                        ":", 1
                    )[0],
                    "response_chars": len(str(result.get("text") or "")),
                },
                effects,
            )

        async def verify(state: PipelineState) -> StageOutput:
            result = _artifact(state, PipelineStage.EXECUTE)
            plan_data = _artifact(state, PipelineStage.PLAN)
            execution_plan = plan_data.get("execution_plan")
            verification = str(result.get("verification_status") or "")
            if (
                isinstance(execution_plan, ExecutionPlan)
                and execution_plan.risk == "mutating"
            ):
                verification = verification or "completed_unverified"
            else:
                verification = verification or "not_required"
            return StageOutput(
                result,
                {
                    "verification_status": verification,
                    "verified": verification in {"verified", "already_satisfied"},
                },
            )

        async def plan_response(state: PipelineState) -> StageOutput:
            result = _artifact(state, PipelineStage.VERIFY, PipelineStage.EXECUTE)
            text = str(result.get("text") or "")
            return StageOutput(
                result,
                {
                    "has_direct_answer": bool(text.strip()),
                    "message_parts": len(result.get("message_parts") or ())
                    or int(bool(text)),
                    "detail_source": "legacy_response_policy",
                },
            )

        async def render_response(state: PipelineState) -> StageOutput:
            result = _artifact(state, PipelineStage.PLAN_RESPONSE)
            if not str(result.get("text") or "").strip():
                raise ValueError("response renderer produced empty text")
            return StageOutput(
                result,
                {
                    "rendered": True,
                    "response_chars": len(str(result["text"])),
                },
            )

        async def deliver(state: PipelineState) -> StageOutput:
            result = _artifact(state, PipelineStage.RENDER_RESPONSE)
            return StageOutput(
                result,
                {"delivery": "deferred_to_connector", "delivered": False},
                status=StageStatus.DEFERRED,
            )

        async def persist(state: PipelineState) -> StageOutput:
            result = _artifact(
                state, PipelineStage.DELIVER, PipelineStage.RENDER_RESPONSE
            )
            return StageOutput(
                result,
                {"persistence": "handled_by_legacy_bridge"},
            )

        async def record_learning(state: PipelineState) -> StageOutput:
            result = _artifact(state, PipelineStage.PERSIST)
            return StageOutput(
                result,
                {"learning": "handled_by_guarded_legacy_service"},
            )

        return {
            PipelineStage.NORMALIZE: normalize,
            PipelineStage.RESOLVE_IDENTITY: resolve_identity,
            PipelineStage.BUILD_CONTEXT: build_context,
            PipelineStage.UNDERSTAND: understand,
            PipelineStage.ROUTE: route,
            PipelineStage.PLAN: plan,
            PipelineStage.AUTHORIZE: authorize,
            PipelineStage.EXECUTE: execute,
            PipelineStage.VERIFY: verify,
            PipelineStage.PLAN_RESPONSE: plan_response,
            PipelineStage.RENDER_RESPONSE: render_response,
            PipelineStage.DELIVER: deliver,
            PipelineStage.PERSIST: persist,
            PipelineStage.RECORD_LEARNING: record_learning,
        }

    @staticmethod
    def _fallback_renderer(state: PipelineState) -> StageOutput:
        result = _artifact(state, PipelineStage.VERIFY, PipelineStage.EXECUTE)
        text = str(result.get("text") or "").strip()
        if not text:
            verification = str(result.get("verification_status") or "")
            text = (
                "The action was verified, but I couldn't format the details."
                if verification in {"verified", "already_satisfied"}
                else "I couldn't format that response. Please check the task status before retrying."
            )
        result["text"] = text
        result["model_used"] = (
            str(result.get("model_used") or "pipeline") + ":render_fallback"
        )
        return StageOutput(
            result,
            {"rendered": True, "fallback": True, "response_chars": len(text)},
            status=StageStatus.DEGRADED,
        )

    async def run(
        self,
        normalized_input: Mapping[str, Any],
        *,
        mode: PipelineMode,
        shadow_result: Mapping[str, Any] | None = None,
        cancellation_event: asyncio.Event | None = None,
    ) -> PipelineState:
        pipeline = TurnPipeline(
            self._handlers(shadow_result=shadow_result),
            fallback_renderer=self._fallback_renderer,
        )
        return await pipeline.run(
            normalized_input,
            mode=mode,
            cancellation_event=cancellation_event,
        )

    @staticmethod
    def _final_artifact(state: PipelineState) -> dict[str, Any]:
        for stage in reversed(tuple(PipelineStage)):
            value = state.artifact(stage)
            if isinstance(value, Mapping) and value.get("text") is not None:
                return _mapping(value)
        return {}

    def finalize_active_result(self, state: PipelineState) -> dict[str, Any]:
        result = self._final_artifact(state)
        if not result:
            result = {
                "text": "I couldn't process that request safely. Please try again.",
                "timestamp": datetime.now(timezone.utc),
                "model_used": "turn_pipeline_error",
                "processing_time_ms": sum(
                    item.duration_ms for item in state.stage_results
                ),
            }
        result["pipeline"] = state.as_dict()
        result["response_origin"] = "turn_pipeline"
        turn_event_writer.record_pipeline(state, response=result)

        understood = _artifact(state, PipelineStage.UNDERSTAND)
        analysis = understood.get("analysis")
        identity = _artifact(state, PipelineStage.RESOLVE_IDENTITY)
        internal_id = identity.get("internal_id")
        if isinstance(analysis, TurnAnalysis) and internal_id:
            result["trace_id"] = analysis.state.trace_id
            result["turn_state"] = analysis.state.as_dict()
            result["response_mode"] = analysis.state.response_mode.value
            self.workflow.dialogue_state.observe_for_owner(
                str(internal_id), analysis.state, result
            )
            result.pop("_dialogue_entities", None)
            turn_event_writer.record(analysis.state, result)
        return result

    @staticmethod
    def shadow_comparison(
        state: PipelineState, legacy_result: Mapping[str, Any]
    ) -> dict[str, Any]:
        routed = _artifact(state, PipelineStage.ROUTE)
        analysis = routed.get("analysis")
        expected_capabilities = []
        if isinstance(analysis, TurnAnalysis):
            expected_capabilities = [
                decision.selected_capability
                for decision in analysis.operational_decisions
                if decision.selected_capability
            ]
        legacy_routing = legacy_result.get("routing")
        if isinstance(legacy_routing, Mapping):
            if isinstance(legacy_routing.get("steps"), list):
                actual_capabilities = [
                    str(item.get("selected_capability") or "")
                    for item in legacy_routing["steps"]
                    if isinstance(item, Mapping)
                ]
            else:
                actual_capabilities = [
                    str(legacy_routing.get("selected_capability") or "")
                ]
            actual_capabilities = [item for item in actual_capabilities if item]
        else:
            actual_capabilities = []
        return {
            "schema_version": 1,
            "route_match": expected_capabilities == actual_capabilities,
            "expected_capability_count": len(expected_capabilities),
            "legacy_capability_count": len(actual_capabilities),
            "legacy_response_present": bool(str(legacy_result.get("text") or "")),
        }
