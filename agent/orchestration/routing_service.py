"""Execute exactly one precomputed unified routing decision."""

from __future__ import annotations

from agent.intent_router import ToolRequest
from agent.orchestration.contracts import ResponseCandidate
from agent.routing import RoutingDecision, route_request


class UnifiedRoutingService:
    def __init__(self, social_service, specialist_router):
        self.social_service = social_service
        self.specialist_router = specialist_router

    async def decide(
        self,
        text: str,
        owner_id: str,
        history=None,
        *,
        allow_model_inference: bool = True,
    ) -> RoutingDecision:
        return await route_request(
            text,
            owner_id,
            history=history,
            allow_model_inference=allow_model_inference,
        )

    async def execute(
        self,
        decision: RoutingDecision,
        text: str,
        internal_id: str,
        platform: str,
        profile: dict,
    ) -> ResponseCandidate | None:
        if decision.intent == "conversation":
            return None
        if decision.intent in {"clarification", "multiple_intents"}:
            return ResponseCandidate(
                str(decision.parameters["message"]),
                f"router:{decision.intent}",
                {
                    "status": "clarification",
                    "verification_status": "not_run",
                },
            )
        if decision.intent == "social":
            return self.social_service.handle(text)
        if decision.intent == "system_command":
            from agent.skills.system_commands import handle_system_command

            result = handle_system_command(
                text, internal_id=internal_id, platform=platform
            )
            return (
                ResponseCandidate(
                    result,
                    "system_commands_skill",
                    {
                        "status": "completed",
                        "verification_status": "not_required",
                    },
                )
                if result is not None
                else None
            )
        if decision.intent == "approval":
            from agent.action_router import execute_request

            request = ToolRequest(
                str(decision.parameters["action"]),
                {"token": decision.parameters["token"]},
            )
            outcome = {}
            result = await execute_request(
                request,
                internal_id,
                {**profile, "_connector": platform},
                _outcome=outcome,
            )
            return ResponseCandidate(result, f"action_router:{request.action}", outcome)
        capability = str(decision.selected_capability)
        if capability == "research":
            from services.research_followup import (
                acknowledgement,
                queue_research,
                should_defer,
            )

            if should_defer(text):
                job = queue_research(
                    internal_id,
                    platform,
                    text,
                    profile,
                    profile.get("_research_origin"),
                )
                if job:
                    return ResponseCandidate(
                        acknowledgement(job, profile),
                        "research_followup",
                        {
                            "status": "queued",
                            "verification_status": "not_run",
                            "research_followup_id": job["job_id"],
                        },
                    )
        if capability.endswith("_skill"):
            return await self.specialist_router.handle_selected(
                capability, text, internal_id, platform
            )
        from agent.action_router import execute_request

        request = ToolRequest(
            capability,
            dict(decision.parameters),
            needs_approval=decision.approval_required,
            explanation=decision.explanation,
            confidence=decision.confidence,
            source=decision.source,
            classifier_trace=dict(decision.classifier_trace),
            authorization=dict(decision.authorization),
            idempotency_key=str(decision.authorization.get("idempotency_key") or ""),
        )
        outcome = {}
        result = await execute_request(
            request,
            internal_id,
            {**profile, "_connector": platform},
            _outcome=outcome,
        )
        if capability == "research" and (
            outcome.get("status") == "failed" or "couldn't find any sources" in result
        ):
            from services.research_followup import acknowledgement, queue_research

            job = queue_research(
                internal_id, platform, text, profile, profile.get("_research_origin")
            )
            if job:
                return ResponseCandidate(
                    acknowledgement(job, profile),
                    "research_followup",
                    {
                        "status": "queued",
                        "verification_status": "not_run",
                        "research_followup_id": job["job_id"],
                    },
                )
        return ResponseCandidate(result, f"action_router:{capability}", outcome)
