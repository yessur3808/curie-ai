"""Live-source research tool."""

from __future__ import annotations

from typing import Any, Mapping

from agent.tooling.contracts import ToolContext, ToolResult


class ResearchTool:
    name = "research"
    read_only = True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from agent.skills.find_info import find_info

        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("A research query is required")
        result = await find_info(query, return_metadata=True)
        return ToolResult(
            text=result["answer"],
            data={"query": query, "sources": result["sources"]},
            source="live_research",
        )
