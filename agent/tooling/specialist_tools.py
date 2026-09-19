"""Adapters that make conversation specialists executable capabilities."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult

Handler = Callable[[str, ToolContext], Awaitable[str | None] | str | None]


class SpecialistTool:
    read_only = True

    def __init__(self, name: str, handler: Handler, *, read_only: bool = True):
        self.name = name
        self.handler = handler
        self.read_only = read_only

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        result = self.handler(str(params.get("text", "")), context)
        if inspect.isawaitable(result):
            result = await result
        return ToolResult(text=str(result or ""), source=self.name)


async def coding(text: str, _context: ToolContext):
    from agent.skills.coding_assistant import handle_coding_query

    return await handle_coding_query(text)


async def navigation(text: str, _context: ToolContext):
    from agent.skills.navigation import handle_navigation_query

    return await handle_navigation_query(text)


async def scheduler(text: str, context: ToolContext):
    from agent.skills.scheduler import handle_reminder_query

    return await handle_reminder_query(
        text, internal_id=context.internal_id, platform=context.platform
    )


async def trip_planner(text: str, context: ToolContext):
    from agent.skills.trip_planner import handle_trip_query

    return await handle_trip_query(text, internal_id=context.internal_id)


async def browser(text: str, _context: ToolContext):
    from agent.skills.browser import handle_browser_query

    return await handle_browser_query(text)


async def network_analyzer(text: str, _context: ToolContext):
    from agent.skills.network_analyzer import handle_network_analyzer_query

    return await handle_network_analyzer_query(text)


async def network_scanner(text: str, _context: ToolContext):
    from agent.skills.network_scanner import handle_network_scanner_query

    return await handle_network_scanner_query(text)


async def http_interceptor(text: str, _context: ToolContext):
    from agent.skills.http_interceptor import handle_http_interceptor_query

    return await handle_http_interceptor_query(text)
