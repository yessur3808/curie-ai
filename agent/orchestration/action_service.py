"""Conversation-facing adapter for the permission-aware action router."""

from __future__ import annotations

from agent.orchestration.contracts import ResponseCandidate


class ActionConversationService:
    async def handle(
        self, text: str, internal_id: str, profile: dict
    ) -> ResponseCandidate | None:
        from agent.action_router import execute_request, resolve_request

        request = await resolve_request(text)
        if request is None:
            return None
        response = await execute_request(request, str(internal_id), profile or {})
        return ResponseCandidate(response, f"action_router:{request.action}")
