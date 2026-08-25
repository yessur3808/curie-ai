"""Typed read-only adapter for deterministic unit and currency conversion."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from agent.tooling.contracts import ToolContext, ToolResult


class ConversionTool:
    name = "conversion"
    read_only = True

    async def execute(self, params: Mapping[str, Any], context: ToolContext) -> ToolResult:
        from agent.skills.conversions import handle_conversion
        text = str(params.get("text", "")).strip()
        if not text:
            raise ValueError("A conversion request is required")
        result = await handle_conversion(text)
        if result is None:
            return ToolResult("Please provide a value, source unit, and target unit.")
        return ToolResult(result, source="conversion")
