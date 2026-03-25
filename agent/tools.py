# agent/tools.py
"""
First-class tools registry for Curie AI.

Provides a unified catalogue of every skill / integration available to the
agent, including discovery of capabilities, status, and metadata. This is the
single source of truth for "what can Curie do right now?"

Usage::

    from agent.tools import registry, get_tool, list_tools

    tools = list_tools()          # list[ToolInfo]
    browser = get_tool("browser") # ToolInfo | None
    print(registry.summary())     # pretty summary dict
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ToolInfo:
    """Metadata record for a single tool / skill."""

    name: str
    """Unique short identifier, e.g. ``"browser"``."""

    display_name: str
    """Human-readable label shown in UIs, e.g. ``"Browser"``."""

    description: str
    """One-line description of what the tool does."""

    category: str
    """High-level category: ``"core"``, ``"connector"``, ``"skill"``, etc."""

    module_path: str
    """Python import path, e.g. ``"agent.skills.browser"``."""

    entry_point: Optional[str] = None
    """Name of the callable inside the module that handles requests, if any."""

    tags: List[str] = field(default_factory=list)
    """Free-form tags for filtering/search."""

    requires_env: List[str] = field(default_factory=list)
    """Environment variables that must ALL be set for the tool to function."""

    requires_one_of_env: List[str] = field(default_factory=list)
    """At least one of these environment variables must be set."""

    available: bool = True
    """False when the underlying module or an env var dependency is missing."""

    error: Optional[str] = None
    """Populated when ``available=False`` to explain why."""

    extra: Dict[str, Any] = field(default_factory=dict)
    """Arbitrary extra metadata (version, links, etc.)."""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "module_path": self.module_path,
            "entry_point": self.entry_point,
            "tags": self.tags,
            "requires_env": self.requires_env,
            "requires_one_of_env": self.requires_one_of_env,
            "available": self.available,
            "error": self.error,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_TOOL_SPECS: List[Dict[str, Any]] = [
    # ── Skills ──────────────────────────────────────────────────────────────
    {
        "name": "browser",
        "display_name": "Browser",
        "description": "Fetch, render, and interact with web pages inside the agent",
        "category": "skill",
        "module_path": "agent.skills.browser",
        "entry_point": "fetch_page",
        "tags": ["web", "browser", "scraping", "automation"],
    },
    {
        "name": "find_info",
        "display_name": "Web Search",
        "description": "Search the web and cross-reference multiple sources via LLM",
        "category": "skill",
        "module_path": "agent.skills.find_info",
        "entry_point": "find_info",
        "tags": ["web", "search", "research"],
    },
    {
        "name": "scheduler",
        "display_name": "Scheduler / Reminders",
        "description": "Natural-language reminder and alarm scheduling backed by MongoDB",
        "category": "skill",
        "module_path": "agent.skills.scheduler",
        "entry_point": "handle_reminder_query",
        "tags": ["reminders", "alarms", "scheduling"],
    },
    {
        "name": "coder",
        "display_name": "Code Generator",
        "description": "Generate code, apply changes to repositories, and open PRs",
        "category": "skill",
        "module_path": "agent.skills.coder",
        "entry_point": "apply_code_change",
        "tags": ["coding", "git", "pr"],
    },
    {
        "name": "coding_assistant",
        "display_name": "Coding Assistant",
        "description": "Analyse code and provide suggestions without touching files",
        "category": "skill",
        "module_path": "agent.skills.coding_assistant",
        "tags": ["coding", "analysis"],
    },
    {
        "name": "code_reviewer",
        "display_name": "Code Reviewer",
        "description": "Review code for quality, style, and security issues",
        "category": "skill",
        "module_path": "agent.skills.code_reviewer",
        "tags": ["coding", "review", "quality"],
    },
    {
        "name": "bug_detector",
        "display_name": "Bug Detector",
        "description": "Automated detection of bugs and logical errors in code",
        "category": "skill",
        "module_path": "agent.skills.bug_detector",
        "tags": ["coding", "debugging"],
    },
    {
        "name": "pair_programming",
        "display_name": "Pair Programming",
        "description": "Interactive coding assistance in a shared coding session",
        "category": "skill",
        "module_path": "agent.skills.pair_programming",
        "tags": ["coding", "interactive"],
    },
    {
        "name": "performance_analyzer",
        "display_name": "Performance Analyzer",
        "description": "Identify and explain performance bottlenecks in code",
        "category": "skill",
        "module_path": "agent.skills.performance_analyzer",
        "tags": ["coding", "performance"],
    },
    {
        "name": "navigation",
        "display_name": "Navigation",
        "description": "Browse and query the local filesystem",
        "category": "skill",
        "module_path": "agent.skills.navigation",
        "tags": ["filesystem", "navigation"],
    },
    {
        "name": "system_commands",
        "display_name": "System Commands",
        "description": "Execute OS-level shell commands with safety guardrails",
        "category": "skill",
        "module_path": "agent.skills.system_commands",
        "tags": ["system", "shell"],
    },
    {
        "name": "trip_planner",
        "display_name": "Trip Planner",
        "description": "Plan travel itineraries and provide travel advice",
        "category": "skill",
        "module_path": "agent.skills.trip_planner",
        "tags": ["travel", "planning"],
    },
    {
        "name": "conversions",
        "display_name": "Unit & Currency Converter",
        "description": "Convert between units and currencies",
        "category": "skill",
        "module_path": "agent.skills.conversions",
        "tags": ["utilities", "conversion"],
    },
    {
        "name": "github_integration",
        "display_name": "GitHub Integration",
        "description": "Interact with GitHub repos, PRs, issues, and actions",
        "category": "skill",
        "module_path": "agent.skills.github_integration",
        "tags": ["git", "github", "devops"],
        "requires_env": ["GITHUB_TOKEN"],
    },
    {
        "name": "gitlab_integration",
        "display_name": "GitLab Integration",
        "description": "Interact with GitLab projects, MRs, and CI pipelines",
        "category": "skill",
        "module_path": "agent.skills.gitlab_integration",
        "tags": ["git", "gitlab", "devops"],
        "requires_env": ["GITLAB_TOKEN"],
    },
    {
        "name": "bitbucket_integration",
        "display_name": "Bitbucket Integration",
        "description": "Interact with Bitbucket repositories and pull requests",
        "category": "skill",
        "module_path": "agent.skills.bitbucket_integration",
        "tags": ["git", "bitbucket", "devops"],
        "requires_env": ["BITBUCKET_TOKEN"],
    },
    {
        "name": "self_updater",
        "display_name": "Self Updater",
        "description": "Update Curie AI itself from the upstream repository",
        "category": "skill",
        "module_path": "agent.skills.self_updater",
        "tags": ["maintenance", "updates"],
    },
    # ── Connectors ──────────────────────────────────────────────────────────
    {
        "name": "api",
        "display_name": "REST / WebSocket API",
        "description": "HTTP REST and WebSocket interface for programmatic access",
        "category": "connector",
        "module_path": "connectors.api",
        "tags": ["api", "http", "websocket"],
    },
    {
        "name": "telegram",
        "display_name": "Telegram",
        "description": "Telegram bot connector for messages and voice notes",
        "category": "connector",
        "module_path": "connectors.telegram",
        "tags": ["messaging", "telegram"],
        "requires_env": ["TELEGRAM_BOT_TOKEN"],
    },
    {
        "name": "discord",
        "display_name": "Discord",
        "description": "Discord bot connector for text channels, DMs, and voice",
        "category": "connector",
        "module_path": "connectors.discord_bot",
        "tags": ["messaging", "discord"],
        "requires_env": ["DISCORD_BOT_TOKEN"],
    },
    {
        "name": "slack",
        "display_name": "Slack",
        "description": "Slack bot connector for channels, DMs, and slash commands",
        "category": "connector",
        "module_path": "connectors.slack_bot",
        "tags": ["messaging", "slack"],
        # SLACK_BOT_TOKEN is always required.  The second token depends on mode:
        # Socket Mode (default): SLACK_APP_TOKEN; HTTP Mode: SLACK_SIGNING_SECRET.
        "requires_env": ["SLACK_BOT_TOKEN"],
        "requires_one_of_env": ["SLACK_APP_TOKEN", "SLACK_SIGNING_SECRET"],
    },
    {
        "name": "whatsapp",
        "display_name": "WhatsApp",
        "description": "WhatsApp connector for personal and group chats",
        "category": "connector",
        "module_path": "connectors.whatsapp",
        "tags": ["messaging", "whatsapp"],
        "requires_env": ["WHATSAPP_SESSION"],
    },
    # ── Core services ────────────────────────────────────────────────────────
    {
        "name": "cron",
        "display_name": "Cron Runner",
        "description": "Scheduled prompt execution with full 5-field cron + named macros",
        "category": "service",
        "module_path": "services.cron_runner",
        "tags": ["scheduling", "automation", "cron"],
    },
    {
        "name": "sessions",
        "display_name": "Session Manager",
        "description": "Per-user / per-channel conversation history backed by MongoDB",
        "category": "service",
        "module_path": "memory.session_manager",
        "tags": ["memory", "sessions", "context"],
    },
    {
        "name": "proactive_messaging",
        "display_name": "Proactive Messaging",
        "description": "Timed check-ins and reminder delivery across all connected platforms",
        "category": "service",
        "module_path": "services.proactive_messaging",
        "tags": ["proactive", "reminders", "messaging"],
    },
    # ── Visual / canvas ──────────────────────────────────────────────────────
    {
        "name": "canvas",
        "display_name": "Live Canvas",
        "description": (
            "Agent-driven visual workspace: interactive node graph that shows "
            "live task and sub-agent activity in the browser"
        ),
        "category": "canvas",
        "module_path": "cli.canvas_webview",
        "entry_point": "show_canvas",
        "tags": ["canvas", "nodes", "visual", "browser"],
    },
    {
        "name": "dashboard",
        "display_name": "Agent Dashboard",
        "description": "Animated SVG portrait dashboard showing Curie and her sub-agents",
        "category": "canvas",
        "module_path": "cli.agent_webview",
        "entry_point": "show_web",
        "tags": ["dashboard", "visual", "browser"],
    },
]


class ToolRegistry:
    """Thread-safe, lazily-initialised registry of all available tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolInfo] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._loaded:
            return
        for spec in _TOOL_SPECS:
            info = self._probe(spec)
            self._tools[info.name] = info
        self._loaded = True

    @staticmethod
    def _probe(spec: Dict[str, Any]) -> ToolInfo:
        """Try to import the module and check env vars; populate availability."""
        available = True
        error: Optional[str] = None

        # 1. Check required env vars (ALL must be present)
        missing_env = [e for e in spec.get("requires_env", []) if not os.getenv(e)]
        if missing_env:
            available = False
            error = f"Missing env vars: {', '.join(missing_env)}"

        # 2. Check conditional env vars (AT LEAST ONE must be present)
        if available:
            one_of = spec.get("requires_one_of_env", [])
            if one_of and not any(os.getenv(e) for e in one_of):
                available = False
                error = f"At least one of these env vars must be set: {', '.join(one_of)}"

        # 3. Try importing the module (only if env checks passed)
        if available:
            try:
                importlib.import_module(spec["module_path"])
            except ImportError as exc:
                available = False
                error = f"Import error: {exc}"
            except Exception as exc:
                # Don't crash the registry for unrelated runtime errors,
                # but do mark the tool as unavailable and record the error.
                available = False
                error = f"Runtime error during import: {exc}"
                logger.exception(
                    "Error importing tool module '%s' for tool '%s'",
                    spec.get("module_path"),
                    spec.get("name"),
                )

        return ToolInfo(
            name=spec["name"],
            display_name=spec.get("display_name", spec["name"]),
            description=spec.get("description", ""),
            category=spec.get("category", "skill"),
            module_path=spec["module_path"],
            entry_point=spec.get("entry_point"),
            tags=spec.get("tags", []),
            requires_env=spec.get("requires_env", []),
            requires_one_of_env=spec.get("requires_one_of_env", []),
            available=available,
            error=error,
            extra=spec.get("extra", {}),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolInfo]:
        """Return the :class:`ToolInfo` for *name*, or ``None``."""
        with self._lock:
            self._load()
            return self._tools.get(name)

    def all(self) -> List[ToolInfo]:
        """Return all registered tools, sorted by category then name."""
        with self._lock:
            self._load()
            return sorted(self._tools.values(), key=lambda t: (t.category, t.name))

    def available_tools(self) -> List[ToolInfo]:
        """Return only tools whose dependencies are satisfied."""
        return [t for t in self.all() if t.available]

    def by_category(self, category: str) -> List[ToolInfo]:
        """Return all tools with matching *category*."""
        return [t for t in self.all() if t.category == category]

    def by_tag(self, tag: str) -> List[ToolInfo]:
        """Return all tools that carry *tag*."""
        return [t for t in self.all() if tag in t.tags]

    def summary(self) -> Dict[str, Any]:
        """Return a JSON-serialisable summary dict for dashboards/APIs."""
        tools = self.all()
        categories: Dict[str, List[Dict[str, Any]]] = {}
        for tool in tools:
            categories.setdefault(tool.category, []).append(tool.as_dict())
        return {
            "total": len(tools),
            "available": sum(1 for t in tools if t.available),
            "unavailable": sum(1 for t in tools if not t.available),
            "categories": categories,
        }

    def reload(self) -> None:
        """Force a fresh probe of all tools (useful after installing deps)."""
        with self._lock:
            self._loaded = False
            self._tools.clear()
            self._load()


# Module-level singleton
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def get_tool(name: str) -> Optional[ToolInfo]:
    """Return the :class:`ToolInfo` for *name*, or ``None``."""
    return registry.get(name)


def list_tools(
    *,
    available_only: bool = False,
    category: Optional[str] = None,
    tag: Optional[str] = None,
) -> List[ToolInfo]:
    """
    Return a filtered list of registered tools.

    Args:
        available_only: When *True* only return tools whose deps are met.
        category: Restrict to a single category (``"skill"``, ``"connector"``,
                  ``"service"``, ``"canvas"``).
        tag: Restrict to tools carrying this tag.

    Returns:
        Sorted list of :class:`ToolInfo` objects.
    """
    tools = registry.available_tools() if available_only else registry.all()
    if category:
        tools = [t for t in tools if t.category == category]
    if tag:
        tools = [t for t in tools if tag in t.tags]
    return tools
