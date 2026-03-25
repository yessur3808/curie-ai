# tests/test_canvas_and_tools.py
"""
Tests for the Live Canvas webview, tools registry, browser skill,
and Slack connector module (without real network access or Slack credentials).
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure repo root is on path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Stub heavyweight dependencies before any application module is imported.
# Mirrors the pattern used by test_connectors.py / test_chat_workflow.py.
# ---------------------------------------------------------------------------
for _mod in (
    "psycopg2",
    "psycopg2.extras",
    "psycopg2.extensions",
    "pymongo",
    "pymongo.collection",
    "pymongo.errors",
    "llm",
    "llm.manager",
    "dotenv",
    "telegram",
    "telegram.ext",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

for _mod in (
    "memory",
    "memory.session_store",
    "memory.database",
    "memory.users",
    "memory.conversations",
    "memory.scraper_patterns",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

if "utils.persona" not in sys.modules:
    sys.modules["utils.persona"] = MagicMock()


# ─── cli.canvas_webview ───────────────────────────────────────────────────────


class TestCanvasWebview:
    """Tests for the Live Canvas HTTP server."""

    def test_html_contains_canvas_element(self):
        from cli.canvas_webview import _HTML

        assert "<canvas" in _HTML
        assert "Curie AI" in _HTML
        assert "connectSSE" in _HTML
        assert "drawNode" in _HTML
        assert "drawEdges" in _HTML

    def test_html_contains_controls(self):
        from cli.canvas_webview import _HTML

        assert "btn-fit" in _HTML
        assert "btn-reset" in _HTML
        assert "btn-toggle-labels" in _HTML

    def test_load_tasks_empty_when_no_file(self):
        import cli.canvas_webview as cv

        orig = cv._TASKS_FILE
        try:
            cv._TASKS_FILE = Path("/tmp/nonexistent_curie_tasks_xyz.json")
            result = cv._load_tasks()
            assert result == {"tasks": {}}
        finally:
            cv._TASKS_FILE = orig

    def test_canvas_server_serves_html(self):
        """Smoke-test: start the canvas server and fetch the root page."""
        import cli.canvas_webview as cv
        import cli.tasks as tm

        tmp = tempfile.mkdtemp()
        orig_cv_file = cv._TASKS_FILE
        orig_tm_dir = tm.CURIE_DIR
        orig_tm_file = tm.TASKS_FILE

        cv._TASKS_FILE = Path(tmp) / "tasks.json"
        tm.CURIE_DIR = Path(tmp)
        tm.TASKS_FILE = cv._TASKS_FILE
        tm.register_task("cv1", "Canvas test task", channel="api")

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        from http.server import ThreadingHTTPServer

        cv._SHUTDOWN_EVENT.clear()
        server = ThreadingHTTPServer(("127.0.0.1", port), cv._Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
            assert "<title>Curie AI" in html

            data = json.loads(
                urllib.request.urlopen(f"http://127.0.0.1:{port}/data").read()
            )
            assert "cv1" in data["tasks"]
        finally:
            cv._SHUTDOWN_EVENT.set()
            server.shutdown()
            server.server_close()
            cv._TASKS_FILE = orig_cv_file
            tm.CURIE_DIR = orig_tm_dir
            tm.TASKS_FILE = orig_tm_file

    def test_canvas_cli_flag(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["tasks", "--canvas"])
        assert args.canvas is True

    def test_canvas_command_parser(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["canvas"])
        assert args.command == "canvas"

    def test_canvas_command_all_flag(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["canvas", "--all"])
        assert args.all is True


# ─── agent.tools ──────────────────────────────────────────────────────────────


class TestToolsRegistry:
    """Tests for the first-class tools registry."""

    def setup_method(self):
        # Force a fresh load for each test
        from agent.tools import registry

        registry.reload()

    def test_list_tools_returns_list(self):
        from agent.tools import list_tools

        tools = list_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_all_tools_have_required_fields(self):
        from agent.tools import list_tools

        for tool in list_tools():
            assert tool.name, f"Tool missing name: {tool}"
            assert tool.display_name, f"Tool {tool.name} missing display_name"
            assert tool.description, f"Tool {tool.name} missing description"
            assert tool.category, f"Tool {tool.name} missing category"
            assert tool.module_path, f"Tool {tool.name} missing module_path"

    def test_get_tool_known(self):
        from agent.tools import get_tool

        tool = get_tool("cron")
        assert tool is not None
        assert tool.name == "cron"
        assert tool.category == "service"

    def test_get_tool_unknown(self):
        from agent.tools import get_tool

        assert get_tool("nonexistent_tool_xyz") is None

    def test_list_tools_filter_category(self):
        from agent.tools import list_tools

        connectors = list_tools(category="connector")
        assert all(t.category == "connector" for t in connectors)
        names = [t.name for t in connectors]
        assert "slack" in names
        assert "discord" in names
        assert "telegram" in names

    def test_list_tools_filter_tag(self):
        from agent.tools import list_tools

        coding_tools = list_tools(tag="coding")
        assert all("coding" in t.tags for t in coding_tools)
        assert len(coding_tools) >= 2

    def test_canvas_tool_registered(self):
        from agent.tools import get_tool

        canvas = get_tool("canvas")
        assert canvas is not None
        assert canvas.category == "canvas"
        assert "nodes" in canvas.tags or "canvas" in canvas.tags

    def test_sessions_tool_registered(self):
        from agent.tools import get_tool

        sessions = get_tool("sessions")
        assert sessions is not None
        assert sessions.category == "service"

    def test_browser_tool_registered(self):
        from agent.tools import get_tool

        browser = get_tool("browser")
        assert browser is not None
        assert "web" in browser.tags

    def test_summary_dict_structure(self):
        from agent.tools import registry

        s = registry.summary()
        assert "total" in s
        assert "available" in s
        assert "unavailable" in s
        assert "categories" in s
        assert s["total"] == s["available"] + s["unavailable"]

    def test_tools_cli_command(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["tools"])
        assert args.command == "tools"

    def test_tools_cli_category_flag(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["tools", "--category", "skill"])
        assert args.category == "skill"

    def test_tools_cli_available_flag(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["tools", "--available"])
        assert args.available_only is True

    def test_available_only_filter(self):
        from agent.tools import list_tools

        available = list_tools(available_only=True)
        assert all(t.available for t in available)


# ─── agent.skills.browser ─────────────────────────────────────────────────────


class TestBrowserSkill:
    """Tests for the browser skill (no real HTTP calls)."""

    def test_is_browser_intent_with_url(self):
        from agent.skills.browser import is_browser_intent

        assert is_browser_intent("open https://example.com")
        assert is_browser_intent("fetch https://news.ycombinator.com")
        assert is_browser_intent("browse to https://python.org")

    def test_is_browser_intent_negative(self):
        from agent.skills.browser import is_browser_intent

        assert not is_browser_intent("what is the weather today?")
        assert not is_browser_intent("remind me to call mom at 3pm")

    @pytest.mark.asyncio
    async def test_fetch_page_blocked_by_ssrf(self):
        from agent.skills.browser import fetch_page

        result = await fetch_page("http://127.0.0.1:9999/secret")
        assert result["error"] is not None
        assert "blocked" in result["error"].lower() or "SSRF" in result["error"]

    @pytest.mark.asyncio
    async def test_fetch_page_non_http_scheme(self):
        from agent.skills.browser import fetch_page

        result = await fetch_page("file:///etc/passwd")
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_extract_links_blocked_private_ip(self):
        from agent.skills.browser import extract_links

        result = await extract_links("http://192.168.1.1/")
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_fetch_page_mocked(self):
        """Verify fetch_page parses HTML correctly when given a mocked response."""
        import httpx
        from agent.skills.browser import fetch_page

        sample_html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
          <h1>Hello World</h1>
          <p>This is a paragraph with enough content to pass the length filter.</p>
        </body>
        </html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = sample_html
        mock_response.is_redirect = False

        # Patch is_safe_url where it is looked up in browser.py's namespace
        with patch(
            "agent.skills.browser.is_safe_url", new=AsyncMock(return_value=True)
        ):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = MagicMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                result = await fetch_page("https://example.com")

        assert result["error"] is None
        assert result["title"] == "Test Page"
        assert "Hello World" in result["content"] or "paragraph" in result["content"]

    @pytest.mark.asyncio
    async def test_page_screenshot_returns_structure(self):
        """Verify page_screenshot returns expected keys."""
        sample_html = """
        <html>
        <head><title>Snapshot Test</title></head>
        <body>
          <h1>Main Heading</h1>
          <h2>Sub Heading</h2>
          <p>Some content that is long enough to pass the filter for paragraphs.</p>
          <a href="https://example.com/page1">Link 1</a>
          <a href="https://example.com/page2">Link 2</a>
        </body>
        </html>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = sample_html
        mock_response.is_redirect = False

        with patch(
            "agent.skills.browser.is_safe_url", new=AsyncMock(return_value=True)
        ):
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = MagicMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                from agent.skills.browser import page_screenshot

                result = await page_screenshot("https://example.com")

        assert result["error"] is None
        assert "title" in result
        assert "headings" in result
        assert "summary" in result
        assert "links" in result


# ─── connectors.slack_bot ─────────────────────────────────────────────────────


class TestSlackBotModule:
    """Tests for the Slack connector (without real Slack credentials)."""

    def test_module_imports_without_slack_bolt(self):
        """The module should import cleanly even when slack-bolt is absent."""
        import importlib
        import sys

        # Temporarily hide slack_bolt if installed
        saved = {}
        for key in list(sys.modules):
            if "slack" in key:
                saved[key] = sys.modules.pop(key)

        # Stub the import so the module can load
        import types

        fake_slack = types.ModuleType("slack_bolt")
        fake_slack.App = None  # type: ignore[attr-defined]
        fake_adapter = types.ModuleType("slack_bolt.adapter.socket_mode")
        fake_adapter.SocketModeHandler = None  # type: ignore[attr-defined]
        sys.modules["slack_bolt"] = fake_slack
        sys.modules["slack_bolt.adapter"] = types.ModuleType("slack_bolt.adapter")
        sys.modules["slack_bolt.adapter.socket_mode"] = fake_adapter

        try:
            if "connectors.slack_bot" in sys.modules:
                del sys.modules["connectors.slack_bot"]
            mod = importlib.import_module("connectors.slack_bot")
            # SLACK_AVAILABLE should be False when App is None
            assert hasattr(mod, "SLACK_AVAILABLE")
            assert hasattr(mod, "set_workflow")
            assert hasattr(mod, "start_slack_bot")
        finally:
            # Restore original modules
            for key in list(sys.modules):
                if "slack" in key:
                    del sys.modules[key]
            sys.modules.update(saved)

    def test_set_workflow_stores_workflow(self):
        """set_workflow should update the module-level _workflow."""
        import connectors.slack_bot as sb

        fake_wf = MagicMock()
        original = sb._workflow
        try:
            sb.set_workflow(fake_wf)
            assert sb._workflow is fake_wf
        finally:
            sb._workflow = original

    def test_start_slack_bot_no_credentials(self):
        """start_slack_bot should return gracefully when token is missing."""
        import connectors.slack_bot as sb

        original_available = sb.SLACK_AVAILABLE
        original_workflow = sb._workflow
        try:
            # Simulate slack-bolt not installed
            sb.SLACK_AVAILABLE = False
            # Should not raise; just log an error
            sb.start_slack_bot()
        finally:
            sb.SLACK_AVAILABLE = original_available
            sb._workflow = original_workflow


# ─── cli.main sessions subcommand ─────────────────────────────────────────────


class TestSessionsCLI:
    def test_sessions_list_parses(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["sessions", "list"])
        assert args.command == "sessions"
        assert args.sessions_action == "list"

    def test_sessions_list_channel_filter(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["sessions", "list", "--channel", "telegram"])
        assert args.channel == "telegram"

    def test_sessions_clear_parses(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["sessions", "clear", "--user-id", "abc123"])
        assert args.sessions_action == "clear"
        assert args.user_id == "abc123"

    def test_sessions_stats_parses(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args(["sessions", "stats"])
        assert args.sessions_action == "stats"
