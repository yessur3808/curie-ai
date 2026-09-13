"""Conversation adapter for specialists in the capability registry."""

from __future__ import annotations
import inspect
from agent.orchestration.contracts import ResponseCandidate
from agent.tooling import ToolContext, get_runtime_registry
from agent.tooling.errors import user_facing_tool_error


class SpecialistRouter:
    def __init__(self, registry=None):
        self.registry = registry or get_runtime_registry()

    def select(self, text: str) -> str | None:
        capability = self.registry.select(text)
        return capability.name if capability else None

    async def handle(
        self, text: str, internal_id: str, platform: str
    ) -> ResponseCandidate | None:
        route = self.select(text)
        if route is None:
            return None
        return await self.handle_selected(route, text, internal_id, platform)

    async def handle_selected(
        self, route: str, text: str, internal_id: str, platform: str
    ) -> ResponseCandidate | None:
        """Execute only the capability chosen by the unified router."""
        if route not in self.registry.names() or not route.endswith("_skill"):
            return None
        legacy_name = "_" + route.removesuffix("_skill")
        override = self.__dict__.get(legacy_name)
        if override is not None:
            args = (
                (text, internal_id, platform)
                if route == "scheduler_skill"
                else ((text, internal_id) if route == "trip_planner_skill" else (text,))
            )
            result = override(*args)
            if inspect.isawaitable(result):
                result = await result
            return ResponseCandidate(str(result), route) if result else None
        try:
            result = await self.registry.execute(
                route, {"text": text}, ToolContext(str(internal_id), platform=platform)
            )
        except Exception as exc:
            return ResponseCandidate(user_facing_tool_error(exc, route), route)
        return ResponseCandidate(result.text, route) if result.text else None

    @staticmethod
    async def _coding(text):
        from agent.tooling.specialist_tools import coding

        return await coding(text, ToolContext("unknown"))

    @staticmethod
    async def _navigation(text):
        from agent.tooling.specialist_tools import navigation

        return await navigation(text, ToolContext("unknown"))

    @staticmethod
    async def _scheduler(text, internal_id, platform):
        from agent.tooling.specialist_tools import scheduler

        return await scheduler(text, ToolContext(internal_id, platform=platform))

    @staticmethod
    async def _trip(text, internal_id):
        from agent.tooling.specialist_tools import trip_planner

        return await trip_planner(text, ToolContext(internal_id))
