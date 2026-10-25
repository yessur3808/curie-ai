"""Live-source research tool."""

from __future__ import annotations

from typing import Any, Mapping
import re

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
        if re.search(r"\b(?:news|headlines)\b", query, re.I):
            from services.current_awareness import news_brief

            brief = await news_brief(query, context.profile)
            if brief:
                return ToolResult(brief["answer"], brief, "live_news_headlines")
        result = await find_info(query, return_metadata=True)
        return ToolResult(
            text=result["answer"],
            data={"query": query, "sources": result["sources"]},
            source="live_research",
        )
