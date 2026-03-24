# agent/tools/__init__.py
"""
First-class tool registry for Curie AI.

Tools are autonomous capabilities that agents can invoke during message
processing.  Each tool module exposes:

  TOOL_NAME : str
      Unique snake_case identifier (e.g. "browser", "canvas").

  is_tool_query(message: str) -> bool
      Lightweight keyword check; return True only when the message clearly
      targets this tool so unrelated queries are never misrouted.

  handle_tool_query(message: str, **kwargs) -> Optional[str]
      Async coroutine.  Process the request and return a formatted response
      string, or None if the tool cannot handle the message.

Available tools
---------------
  browser       – fetch web pages / run searches
  canvas        – agent-driven live visual workspace
  nodes         – node graph / workflow builder
  cron_tool     – create, list, and remove scheduled jobs
  sessions_tool – inspect and reset conversation sessions
  discord_actions – send messages and perform actions via Discord
  slack_actions   – send messages and perform actions via Slack
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = [
    "TOOL_MODULES",
]

# Ordered list of tool module dotted paths — loaded lazily on first use.
TOOL_MODULES: list[str] = [
    "agent.tools.browser",
    "agent.tools.canvas",
    "agent.tools.nodes",
    "agent.tools.cron_tool",
    "agent.tools.sessions_tool",
    "agent.tools.discord_actions",
    "agent.tools.slack_actions",
]
