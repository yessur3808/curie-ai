"""Compatibility façade for the authoritative executable capability registry.

New code should import :mod:`agent.tooling` directly. This module intentionally
contains no catalogue or independent availability state.
"""

from __future__ import annotations
from typing import Optional

from agent.tooling.contracts import CapabilityDefinition
from agent.tooling.registry import get_runtime_registry

ToolInfo = CapabilityDefinition


class _RegistryProxy:
    """Keep legacy imports attached to the current lifecycle-managed registry."""

    def __getattr__(self, name: str):
        return getattr(get_runtime_registry(), name)


registry = _RegistryProxy()

_ALIASES = {
    "browser": "browser_skill",
    "navigation": "navigation_skill",
    "trip_planner": "trip_planner_skill",
}


def get_tool(name: str) -> Optional[CapabilityDefinition]:
    try:
        return registry.get(_ALIASES.get(name, name))
    except KeyError:
        return None


def list_tools(
    *, available_only: bool = False, category: str | None = None, tag: str | None = None
) -> list[CapabilityDefinition]:
    tools = registry.available_tools() if available_only else registry.all()
    if category:
        tools = [tool for tool in tools if tool.category == category]
    if tag:
        tools = [tool for tool in tools if tag in tool.tags]
    return tools
