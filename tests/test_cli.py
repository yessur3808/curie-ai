# tests/test_cli.py
"""
Tests for the Curie CLI module (cli/).
These tests do not require a running Curie instance or real hardware.
"""

import json
import os
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import importlib

import pytest

# Ensure repo root is on path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ─── cli.tasks ───────────────────────────────────────────────────────────────


class TestTaskRegistry:
    """Tests for cli.tasks task/sub-agent registry."""

    def setup_method(self):
        """Point task registry at a temp file for isolation."""
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_curie_dir = None
        import cli.tasks as _tasks_mod

        self._tasks_mod = _tasks_mod
        self._orig_curie_dir = _tasks_mod.CURIE_DIR
        self._orig_tasks_file = _tasks_mod.TASKS_FILE
        tmp_path = Path(self._tmpdir.name)
        _tasks_mod.CURIE_DIR = tmp_path
        _tasks_mod.TASKS_FILE = tmp_path / "tasks.json"

    def teardown_method(self):
        self._tasks_mod.CURIE_DIR = self._orig_curie_dir
        self._tasks_mod.TASKS_FILE = self._orig_tasks_file
        self._tmpdir.cleanup()

    def test_register_and_get_task(self):
        t = self._tasks_mod
        t.register_task("t1", "test task", channel="cli")
        tasks = t.get_tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "t1"
        assert tasks[0]["status"] == "running"
        assert tasks[0]["description"] == "test task"

    def test_register_sub_agent(self):
        t = self._tasks_mod
        t.register_task("t2", "task with agent")
        t.register_sub_agent("t2", "agent1", role="llm_inference", model="gpt-4")
        tasks = t.get_tasks()
        assert "agent1" in tasks[0]["sub_agents"]
        assert tasks[0]["sub_agents"]["agent1"]["role"] == "llm_inference"

    def test_update_sub_agent(self):
        t = self._tasks_mod
        t.register_task("t3", "another task")
        t.register_sub_agent("t3", "agentX", role="navigator")
        t.update_sub_agent("t3", "agentX", status="done", result_summary="ok")
        tasks = t.get_tasks()
        agent = tasks[0]["sub_agents"]["agentX"]
        assert agent["status"] == "done"
        assert agent["result_summary"] == "ok"

    def test_finish_task(self):
        t = self._tasks_mod
        t.register_task("t4", "finishing task")
        t.finish_task("t4")
        tasks = t.get_tasks()
        assert tasks[0]["status"] == "done"

    def test_get_running_tasks(self):
        t = self._tasks_mod
        t.register_task("t5", "running")
        t.register_task("t6", "finished")
        t.finish_task("t6")
        running = t.get_running_tasks()
        assert any(task["id"] == "t5" for task in running)
        assert all(task["id"] != "t6" for task in running)

    def test_clear_finished_tasks(self):
        t = self._tasks_mod
        t.register_task("ta", "active")
        t.register_task("tb", "done task")
        t.finish_task("tb")
        removed = t.clear_finished_tasks()
        assert removed == 1
        remaining = t.get_tasks()
        assert len(remaining) == 1
        assert remaining[0]["id"] == "ta"

    def test_summary(self):
        t = self._tasks_mod
        t.register_task("ts1", "summary task")
        t.register_sub_agent("ts1", "s1", role="r1")
        t.register_sub_agent("ts1", "s2", role="r2")
        t.update_sub_agent("ts1", "s2", "done")
        summary = t.get_task_summary()
        assert summary["total_tasks"] == 1
        assert summary["running_tasks"] == 1
        assert summary["total_sub_agents"] == 2
        assert summary["running_sub_agents"] == 1


# ─── cli.daemon ───────────────────────────────────────────────────────────────


class TestDaemonHelpers:
    """Tests for PID-file read/write/removal and status helpers."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmpdir.name)

        import cli.daemon as _daemon_mod

        self._daemon_mod = _daemon_mod
        self._orig_curie_dir = _daemon_mod.CURIE_DIR
        self._orig_pid_file = _daemon_mod.PID_FILE
        self._orig_log_file = _daemon_mod.LOG_FILE
        self._orig_state_file = _daemon_mod.STATE_FILE

        _daemon_mod.CURIE_DIR = self._tmp_path
        _daemon_mod.PID_FILE = self._tmp_path / "curie.pid"
        _daemon_mod.LOG_FILE = self._tmp_path / "curie.log"
        _daemon_mod.STATE_FILE = self._tmp_path / "daemon_state.json"

    def teardown_method(self):
        d = self._daemon_mod
        d.CURIE_DIR = self._orig_curie_dir
        d.PID_FILE = self._orig_pid_file
        d.LOG_FILE = self._orig_log_file
        d.STATE_FILE = self._orig_state_file
        self._tmpdir.cleanup()

    def test_read_pid_no_file(self):
        assert self._daemon_mod._read_pid() is None

    def test_write_and_read_pid(self):
        d = self._daemon_mod
        d._write_pid(12345)
        assert d._read_pid() == 12345

    def test_remove_pid(self):
        d = self._daemon_mod
        d._write_pid(99)
        d._remove_pid()
        assert d._read_pid() is None

    def test_status_not_running(self):
        st = self._daemon_mod.get_status()
        assert st["running"] is False
        assert st["pid"] is None

    def test_status_stale_pid(self):
        d = self._daemon_mod
        # Use a PID that is definitely not running (0 would cause errors, use a very large one)
        d._write_pid(999999999)
        st = d.get_status()
        assert st["running"] is False
        # Stale PID file should have been cleaned up
        assert d._read_pid() is None


# ─── cli.main ─────────────────────────────────────────────────────────────────


class TestCLIParser:
    """Test that the argument parser builds and routes commands correctly."""

    def _parse(self, argv):
        from cli.main import _build_parser

        return _build_parser().parse_args(argv)

    def test_no_args_returns_none_command(self):
        from cli.main import _build_parser

        args = _build_parser().parse_args([])
        assert args.command is None

    def test_start_command(self):
        args = self._parse(["start"])
        assert args.command == "start"

    def test_start_with_api_flag(self):
        args = self._parse(["start", "--api"])
        assert args.api is True

    def test_stop_command(self):
        args = self._parse(["stop"])
        assert args.command == "stop"

    def test_status_command(self):
        args = self._parse(["status"])
        assert args.command == "status"

    def test_metrics_defaults(self):
        args = self._parse(["metrics"])
        assert args.command == "metrics"
        assert args.once is False
        assert args.interval == 1.0

    def test_metrics_once(self):
        args = self._parse(["metrics", "--once"])
        assert args.once is True

    def test_tasks_defaults(self):
        args = self._parse(["tasks"])
        assert args.command == "tasks"
        assert args.live is False
        assert args.all is False

    def test_tasks_live(self):
        args = self._parse(["tasks", "--live"])
        assert args.live is True

    def test_agent_interactive(self):
        args = self._parse(["agent"])
        assert args.command == "agent"
        assert args.message is None

    def test_agent_single_message(self):
        args = self._parse(["agent", "-m", "hello curie"])
        assert args.message == "hello curie"

    def test_doctor_command(self):
        args = self._parse(["doctor"])
        assert args.command == "doctor"

    def test_service_install(self):
        args = self._parse(["service", "install"])
        assert args.command == "service"
        assert args.service_action == "install"

    def test_service_start(self):
        args = self._parse(["service", "start"])
        assert args.service_action == "start"

    def test_logs_defaults(self):
        args = self._parse(["logs"])
        assert args.command == "logs"
        assert args.lines == 50
        assert args.follow is False

    def test_logs_custom(self):
        args = self._parse(["logs", "-n", "100", "-f"])
        assert args.lines == 100
        assert args.follow is True

    def test_main_returns_0_no_args(self):
        from cli.main import main

        assert main([]) == 0

    def test_start_delegates_to_daemon(self):
        from cli.main import main

        with patch("cli.daemon.start_daemon") as mock_start:
            mock_start.return_value = {"success": True, "pid": 42, "message": "started"}
            rc = main(["start"])
        assert rc == 0
        mock_start.assert_called_once()

    def test_stop_delegates_to_daemon(self):
        from cli.main import main

        with patch("cli.daemon.stop_daemon") as mock_stop:
            mock_stop.return_value = {"success": True, "message": "stopped"}
            rc = main(["stop"])
        assert rc == 0

    def test_doctor_runs(self):
        from cli.main import main

        with patch("cli.doctor.run_doctor") as mock_doctor:
            mock_doctor.return_value = 0
            rc = main(["doctor"])
        assert rc == 0


# ─── cli.metrics ──────────────────────────────────────────────────────────────


class TestMetrics:
    """Smoke tests for the metrics module."""

    def test_build_table_returns_table(self):
        """_build_metrics_table should return a Table object without raising."""
        try:
            from rich.table import Table
            from cli.metrics import _build_metrics_table
            import psutil

            psutil.cpu_percent(interval=None)
            table = _build_metrics_table()
            assert isinstance(table, Table)
        except ImportError:
            pytest.skip("rich or psutil not installed")

    def test_fmt_bytes(self):
        from cli.metrics import _fmt_bytes

        assert "KB" in _fmt_bytes(2048)
        assert "MB" in _fmt_bytes(2 * 1024 * 1024)
        assert "GB" in _fmt_bytes(2 * 1024**3)


# ─── cli.doctor ───────────────────────────────────────────────────────────────


class TestDoctor:
    """Test that run_doctor returns an int and doesn't raise."""

    def test_doctor_returns_int(self):
        from cli.doctor import run_doctor

        result = run_doctor()
        assert isinstance(result, int)
        assert result in (0, 1)


# ─── cli.tasks (description field + update_sub_agent_description) ─────────────


class TestTaskRegistryDescription:
    """Tests for the new description field on sub-agents."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import cli.tasks as _tasks_mod
        self._tasks_mod = _tasks_mod
        self._orig_curie_dir = _tasks_mod.CURIE_DIR
        self._orig_tasks_file = _tasks_mod.TASKS_FILE
        tmp_path = Path(self._tmpdir.name)
        _tasks_mod.CURIE_DIR = tmp_path
        _tasks_mod.TASKS_FILE = tmp_path / "tasks.json"

    def teardown_method(self):
        self._tasks_mod.CURIE_DIR = self._orig_curie_dir
        self._tasks_mod.TASKS_FILE = self._orig_tasks_file
        self._tmpdir.cleanup()

    def test_register_sub_agent_with_description(self):
        t = self._tasks_mod
        t.register_task("d1", "task with description")
        t.register_sub_agent("d1", "s1", role="coding_assistant",
                             description="Scanning for coding query")
        tasks = t.get_tasks()
        agent = tasks[0]["sub_agents"]["s1"]
        assert agent["description"] == "Scanning for coding query"

    def test_register_sub_agent_description_defaults_empty(self):
        t = self._tasks_mod
        t.register_task("d2", "task no desc")
        t.register_sub_agent("d2", "s2", role="navigation")
        tasks = t.get_tasks()
        agent = tasks[0]["sub_agents"]["s2"]
        assert agent["description"] == ""

    def test_update_sub_agent_description(self):
        t = self._tasks_mod
        t.register_task("d3", "task update desc")
        t.register_sub_agent("d3", "s3", role="llm_inference",
                             description="Initial description")
        t.update_sub_agent_description("d3", "s3", "Running LLM inference")
        tasks = t.get_tasks()
        agent = tasks[0]["sub_agents"]["s3"]
        assert agent["description"] == "Running LLM inference"

    def test_update_sub_agent_description_unknown_task(self):
        """Should not raise when task or agent does not exist."""
        t = self._tasks_mod
        t.update_sub_agent_description("nonexistent", "noagent", "test")

    def test_update_sub_agent_description_unknown_agent(self):
        t = self._tasks_mod
        t.register_task("d4", "task missing agent")
        t.update_sub_agent_description("d4", "ghost", "test")  # must not raise


# ─── cli.tasks_display (tree visualization) ───────────────────────────────────


class TestAgentTreeVisualization:
    """Tests for the new Rich tree visualization."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import cli.tasks as _tasks_mod
        self._tasks_mod = _tasks_mod
        self._orig_curie_dir = _tasks_mod.CURIE_DIR
        self._orig_tasks_file = _tasks_mod.TASKS_FILE
        tmp_path = Path(self._tmpdir.name)
        _tasks_mod.CURIE_DIR = tmp_path
        _tasks_mod.TASKS_FILE = tmp_path / "tasks.json"

    def teardown_method(self):
        self._tasks_mod.CURIE_DIR = self._orig_curie_dir
        self._tasks_mod.TASKS_FILE = self._orig_tasks_file
        self._tmpdir.cleanup()

    def _seed_tasks(self):
        t = self._tasks_mod
        t.register_task("tr1", "Weather in Tokyo", channel="telegram")
        t.register_sub_agent("tr1", "coding_skill", role="coding_assistant",
                              description="Scanning for coding / programming query")
        t.register_sub_agent("tr1", "llm_provider", role="llm_inference",
                              description="Running LLM inference")
        t.update_sub_agent("tr1", "coding_skill", "done", result_summary="skipped")

    def test_build_agent_tree_empty(self):
        try:
            from cli.tasks_display import _build_agent_tree
            from rich.tree import Tree
        except ImportError:
            pytest.skip("rich not installed")
        tree = _build_agent_tree([], show_finished=False)
        assert isinstance(tree, Tree)

    def test_build_agent_tree_with_tasks(self):
        try:
            from cli.tasks_display import _build_agent_tree
            from rich.tree import Tree
        except ImportError:
            pytest.skip("rich not installed")
        self._seed_tasks()
        tasks = self._tasks_mod.get_tasks()
        tree = _build_agent_tree(tasks, show_finished=True, tick=3)
        assert isinstance(tree, Tree)

    def test_dur_helper(self):
        from cli.tasks_display import _dur
        assert _dur(None, None) == "—"
        now = time.time()
        assert "s" in _dur(now - 5, now)

    def test_friendly_role_known(self):
        from cli.tasks_display import _friendly_role
        label = _friendly_role("llm_inference")
        assert "LLM" in label

    def test_friendly_role_unknown(self):
        from cli.tasks_display import _friendly_role
        label = _friendly_role("custom_agent")
        assert "custom_agent" in label

    def test_show_tasks_tree_flag_calls_show_agent_tree(self):
        """show_tasks(tree=True) should delegate to show_agent_tree."""
        try:
            from cli.tasks_display import show_tasks
        except ImportError:
            pytest.skip("rich not installed")
        with patch("cli.tasks_display.show_agent_tree") as mock_tree:
            show_tasks(show_finished=False, live=False, tree=True)
            mock_tree.assert_called_once_with(show_finished=False, live=False)

    def test_tasks_tree_cli_flag(self):
        """curie tasks --tree should parse correctly."""
        from cli.main import _build_parser
        args = _build_parser().parse_args(["tasks", "--tree"])
        assert args.tree is True

    def test_tasks_tree_live_cli_flags(self):
        from cli.main import _build_parser
        args = _build_parser().parse_args(["tasks", "--tree", "--live"])
        assert args.tree is True
        assert args.live is True


class TestWebViewFlags:
    """Tests for --visual and --web CLI flags and web server."""

    def test_tasks_visual_flag(self):
        from cli.main import _build_parser
        args = _build_parser().parse_args(["tasks", "--visual"])
        assert args.visual is True

    def test_tasks_web_flag(self):
        from cli.main import _build_parser
        args = _build_parser().parse_args(["tasks", "--web"])
        assert args.web is True

    def test_tasks_web_all_flags(self):
        from cli.main import _build_parser
        args = _build_parser().parse_args(["tasks", "--web", "--all"])
        assert args.web is True
        assert args.all is True

    def test_webview_html_contains_curie_svg(self):
        from cli.agent_webview import _HTML
        assert "buildCurieSVG" in _HTML
        assert "buildSubSVG" in _HTML
        assert "connectSSE" in _HTML
        assert "Curie AI" in _HTML

    def test_webview_server(self):
        import tempfile, threading, time, urllib.request, json
        from pathlib import Path
        import cli.agent_webview as wv
        import cli.tasks as tm

        tmp = tempfile.mkdtemp()
        wv._TASKS_FILE = Path(tmp) / "tasks.json"
        tm.CURIE_DIR = Path(tmp)
        tm.TASKS_FILE = wv._TASKS_FILE
        tm.register_task("wt1", "Web test task", channel="api")

        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        from http.server import HTTPServer
        wv._SHUTDOWN_EVENT.clear()
        server = HTTPServer(("127.0.0.1", port), wv._Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
            assert "<title>Curie AI" in html
            data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/data").read())
            assert "wt1" in data["tasks"]
        finally:
            wv._SHUTDOWN_EVENT.set()
            server.shutdown()
