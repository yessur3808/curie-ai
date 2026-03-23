# tests/test_system_commands.py
"""
Tests for agent/skills/system_commands.py.

We mock all external dependencies (psutil, cli.daemon, cli.tasks) so these
tests run without a real system, daemon, or database.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.skills.system_commands import (
    detect_system_command,
    handle_system_command,
    _is_master,
    _render_status,
    _render_metrics,
    _render_tasks,
    _render_doctor,
    _render_logs,
)


# ─── detect_system_command ────────────────────────────────────────────────────


class TestDetectSystemCommand:
    """Pure pattern detection – no I/O needed."""

    # Explicit slash commands
    def test_slash_status(self):
        assert detect_system_command("/status") == "status"

    def test_slash_curie_status(self):
        assert detect_system_command("/curie status") == "status"

    def test_slash_metrics(self):
        assert detect_system_command("/metrics") == "metrics"

    def test_slash_tasks(self):
        assert detect_system_command("/tasks") == "tasks"

    def test_slash_doctor(self):
        assert detect_system_command("/doctor") == "doctor"

    def test_slash_logs(self):
        assert detect_system_command("/logs") == "logs"

    def test_slash_start(self):
        assert detect_system_command("/start") == "start"

    def test_slash_stop(self):
        assert detect_system_command("/stop") == "stop"

    def test_slash_restart(self):
        assert detect_system_command("/restart") == "restart"

    # Natural language – status
    def test_nl_is_curie_running(self):
        assert detect_system_command("is curie running?") == "status"

    def test_nl_curie_status(self):
        assert detect_system_command("curie status") == "status"

    def test_nl_daemon_status(self):
        assert detect_system_command("what is the daemon status?") == "status"

    def test_nl_bot_status(self):
        assert detect_system_command("show me the bot status") == "status"

    # Natural language – metrics
    def test_nl_system_metrics(self):
        assert detect_system_command("show system metrics") == "metrics"

    def test_nl_cpu_usage(self):
        assert detect_system_command("what is the cpu usage?") == "metrics"

    def test_nl_ram_usage(self):
        assert detect_system_command("how much ram usage is there") == "metrics"

    def test_nl_memory_usage(self):
        assert detect_system_command("memory usage please") == "metrics"

    def test_nl_disk_usage(self):
        assert detect_system_command("disk usage stats") == "metrics"

    def test_nl_resource_usage(self):
        assert detect_system_command("show resource usage") == "metrics"

    # Natural language – tasks
    def test_nl_active_tasks(self):
        assert detect_system_command("show active tasks") == "tasks"

    def test_nl_running_tasks(self):
        assert detect_system_command("what tasks are running?") == "tasks"

    def test_nl_sub_agents(self):
        assert detect_system_command("show sub-agents") == "tasks"

    def test_nl_task_breakdown(self):
        assert detect_system_command("give me a task breakdown") == "tasks"

    # Natural language – doctor
    def test_nl_run_doctor(self):
        assert detect_system_command("run doctor") == "doctor"

    def test_nl_health_check(self):
        assert detect_system_command("run a health check") == "doctor"

    def test_nl_system_health(self):
        assert detect_system_command("system health?") == "doctor"

    def test_nl_diagnose(self):
        assert detect_system_command("diagnose my system") == "doctor"

    # Natural language – logs
    def test_nl_show_logs(self):
        assert detect_system_command("show me the logs") == "logs"

    def test_nl_recent_logs(self):
        assert detect_system_command("show recent logs") == "logs"

    def test_nl_last_n_logs(self):
        assert detect_system_command("last 30 log lines") == "logs"

    def test_nl_daemon_logs(self):
        assert detect_system_command("daemon logs") == "logs"

    # Natural language – start/stop/restart
    def test_nl_start_curie(self):
        assert detect_system_command("start curie") == "start"

    def test_nl_stop_curie(self):
        assert detect_system_command("stop curie") == "stop"

    def test_nl_restart_curie(self):
        assert detect_system_command("restart curie") == "restart"

    # Non-system messages should return None
    def test_unrelated_message(self):
        assert detect_system_command("what's the weather today?") is None

    def test_empty_string(self):
        assert detect_system_command("") is None

    def test_hello(self):
        assert detect_system_command("hello, how are you?") is None


# ─── _is_master ───────────────────────────────────────────────────────────────


class TestIsMaster:
    def test_matching_master(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "user-123"}):
            assert _is_master("user-123") is True

    def test_non_matching(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "user-123"}):
            assert _is_master("other-user") is False

    def test_no_env_var(self):
        env = {k: v for k, v in os.environ.items() if k != "MASTER_USER_ID"}
        with patch.dict(os.environ, env, clear=True):
            assert _is_master("anyone") is False


# ─── _render_status ───────────────────────────────────────────────────────────


class TestRenderStatus:
    def test_running(self):
        mock_st = {
            "running": True,
            "pid": 1234,
            "uptime_seconds": 3661,
            "log_file": "/tmp/curie.log",
        }
        with patch("agent.skills.system_commands.get_status", return_value=mock_st):
            result = _render_status()
        assert "running" in result.lower()
        assert "1234" in result

    def test_not_running(self):
        mock_st = {
            "running": False,
            "pid": None,
            "uptime_seconds": None,
            "log_file": "/tmp/curie.log",
        }
        with patch("agent.skills.system_commands.get_status", return_value=mock_st):
            result = _render_status()
        assert "not running" in result.lower()


# ─── _render_metrics ─────────────────────────────────────────────────────────


class TestRenderMetrics:
    def _mock_psutil(self):
        m = MagicMock()
        m.cpu_percent.side_effect = [0.0, 25.5]
        m.cpu_count.return_value = 4
        freq = MagicMock()
        freq.current = 2600.0
        m.cpu_freq.return_value = freq
        vm = MagicMock()
        vm.percent = 40.0
        vm.used = 4 * 1024**3
        vm.total = 16 * 1024**3
        vm.available = 12 * 1024**3
        m.virtual_memory.return_value = vm
        sw = MagicMock()
        sw.percent = 0.0
        sw.used = 0
        sw.total = 2 * 1024**3
        m.swap_memory.return_value = sw
        disk = MagicMock()
        disk.percent = 55.0
        disk.used = 80 * 1024**3
        disk.total = 500 * 1024**3
        disk.free = 420 * 1024**3
        m.disk_usage.return_value = disk
        net = MagicMock()
        net.bytes_sent = 10 * 1024**2
        net.bytes_recv = 50 * 1024**2
        m.net_io_counters.return_value = net
        return m

    def test_contains_key_metrics(self):
        mock_psutil = self._mock_psutil()
        with (
            patch("agent.skills.system_commands._psutil", mock_psutil),
            patch("agent.skills.system_commands._PSUTIL_AVAILABLE", True),
        ):
            result = _render_metrics()
        # Should contain CPU and RAM info
        assert "CPU" in result or "cpu" in result.lower()

    def test_no_psutil(self):
        """When psutil is unavailable, _render_metrics returns an install hint."""
        with patch("agent.skills.system_commands._PSUTIL_AVAILABLE", False):
            result = _render_metrics()
        assert "psutil" in result.lower()

    def test_returns_string(self):
        """At minimum _render_metrics must return a string (even if psutil is real)."""
        result = _render_metrics()
        assert isinstance(result, str)
        assert len(result) > 0


# ─── _render_tasks ────────────────────────────────────────────────────────────


class TestRenderTasks:
    def test_no_tasks(self):
        with (
            patch("agent.skills.system_commands.get_tasks", return_value=[]),
            patch(
                "agent.skills.system_commands.get_task_summary",
                return_value={
                    "running_tasks": 0,
                    "total_tasks": 0,
                    "running_sub_agents": 0,
                    "total_sub_agents": 0,
                },
            ),
        ):
            result = _render_tasks()
        assert "No active tasks" in result

    def test_with_running_task(self):
        now = time.time()
        mock_tasks = [
            {
                "id": "abc12345",
                "description": "What is the weather?",
                "channel": "telegram",
                "status": "running",
                "started_at": now - 5,
                "sub_agents": {
                    "llm": {
                        "role": "llm_inference",
                        "status": "running",
                        "model": "llama3",
                        "result_summary": "",
                    },
                },
            }
        ]
        mock_summary = {
            "running_tasks": 1,
            "total_tasks": 1,
            "running_sub_agents": 1,
            "total_sub_agents": 1,
        }
        with (
            patch("agent.skills.system_commands.get_tasks", return_value=mock_tasks),
            patch(
                "agent.skills.system_commands.get_task_summary",
                return_value=mock_summary,
            ),
        ):
            result = _render_tasks()
        assert "abc12345" in result
        assert "telegram" in result
        assert "llm_inference" in result


# ─── _render_doctor ───────────────────────────────────────────────────────────


class TestRenderDoctor:
    def test_returns_health_report(self):
        mock_st = {
            "running": False,
            "pid": None,
            "uptime_seconds": None,
            "log_file": "/tmp/curie.log",
        }
        with patch("agent.skills.system_commands.get_status", return_value=mock_st):
            result = _render_doctor()
        assert "Health Report" in result
        assert "Python" in result

    def test_contains_env_section(self):
        mock_st = {
            "running": False,
            "pid": None,
            "uptime_seconds": None,
            "log_file": "/tmp/curie.log",
        }
        with patch("agent.skills.system_commands.get_status", return_value=mock_st):
            result = _render_doctor()
        assert "Environment" in result or "POSTGRES_DSN" in result


# ─── _render_logs ────────────────────────────────────────────────────────────


class TestRenderLogs:
    def test_file_not_found(self, tmp_path):
        nonexistent = tmp_path / "missing.log"
        with patch("agent.skills.system_commands.LOG_FILE", nonexistent):
            result = _render_logs(10)
        assert "not found" in result.lower()

    def test_reads_last_n_lines(self, tmp_path):
        log_file = tmp_path / "curie.log"
        lines = [f"line {i}" for i in range(100)]
        log_file.write_text("\n".join(lines))
        with patch("agent.skills.system_commands.LOG_FILE", log_file):
            result = _render_logs(5)
        assert "line 99" in result
        assert "line 95" in result
        # Should not contain early lines
        assert "line 0" not in result

    def test_empty_file(self, tmp_path):
        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        with patch("agent.skills.system_commands.LOG_FILE", log_file):
            result = _render_logs(10)
        assert "empty" in result.lower()


# ─── handle_system_command (integration-style) ────────────────────────────────


class TestHandleSystemCommand:
    """Test the top-level dispatcher."""

    def _mock_daemon_st(self, running=False):
        return {
            "running": running,
            "pid": 42 if running else None,
            "uptime_seconds": 120 if running else None,
            "log_file": "/tmp/curie.log",
        }

    def test_returns_none_for_unrelated(self):
        result = handle_system_command("what's the weather?", internal_id="u1")
        assert result is None

    def test_status_command(self):
        with patch(
            "agent.skills.system_commands.get_status",
            return_value=self._mock_daemon_st(running=True),
        ):
            result = handle_system_command("/status", internal_id="u1")
        assert result is not None
        assert "running" in result.lower()

    def test_doctor_command(self):
        with patch(
            "agent.skills.system_commands.get_status",
            return_value=self._mock_daemon_st(),
        ):
            result = handle_system_command("/doctor", internal_id="u1")
        assert result is not None
        assert "Health" in result

    def test_tasks_command(self):
        with (
            patch("agent.skills.system_commands.get_tasks", return_value=[]),
            patch(
                "agent.skills.system_commands.get_task_summary",
                return_value={
                    "running_tasks": 0,
                    "total_tasks": 0,
                    "running_sub_agents": 0,
                    "total_sub_agents": 0,
                },
            ),
        ):
            result = handle_system_command("/tasks", internal_id="u1")
        assert result is not None
        assert "Task" in result

    def test_privileged_command_blocked_for_non_master(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}):
            result = handle_system_command("/start", internal_id="non-master")
        assert result is not None
        assert "restricted" in result.lower() or "master" in result.lower()

    def test_privileged_command_allowed_for_master(self):
        with (
            patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}),
            patch(
                "agent.skills.system_commands.start_daemon",
                return_value={"success": True, "pid": 99, "message": "started ok"},
            ),
        ):
            result = handle_system_command("/start", internal_id="master-user")
        assert result is not None
        assert "started" in result.lower() or "ok" in result.lower()

    def test_stop_blocked_non_master(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}):
            result = handle_system_command("stop curie", internal_id="regular-user")
        assert "restricted" in result.lower() or "master" in result.lower()

    def test_help_trigger(self):
        result = handle_system_command("curie help", internal_id="u1")
        assert result is not None
        assert "System Commands" in result or "commands" in result.lower()

    def test_natural_language_metrics(self):
        result = handle_system_command("show system metrics", internal_id="u1")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 10

    def test_natural_language_status(self):
        with patch(
            "agent.skills.system_commands.get_status",
            return_value=self._mock_daemon_st(),
        ):
            result = handle_system_command("is curie running?", internal_id="u1")
        assert result is not None

    def test_logs_with_n_lines(self, tmp_path):
        log_file = tmp_path / "curie.log"
        log_file.write_text("\n".join(f"L{i}" for i in range(50)))
        with patch("agent.skills.system_commands.LOG_FILE", log_file):
            result = handle_system_command("show last 10 logs", internal_id="u1")
        assert result is not None
        assert "L49" in result

    def test_exception_returns_error_string(self):
        """If a renderer raises, the dispatcher returns an error string (not None)."""
        with patch(
            "agent.skills.system_commands._render_status",
            side_effect=RuntimeError("boom"),
        ):
            result = handle_system_command("/status", internal_id="u1")
        assert result is not None
        assert "Error" in result or "error" in result.lower()

    # Natural-language security tests for privileged commands
    def test_nl_start_blocked_non_master(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}):
            result = handle_system_command("start curie", internal_id="other-user")
        assert result is not None
        assert "restricted" in result.lower() or "master" in result.lower()

    def test_nl_stop_blocked_non_master(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}):
            result = handle_system_command("stop curie", internal_id="other-user")
        assert result is not None
        assert "restricted" in result.lower() or "master" in result.lower()

    def test_nl_restart_blocked_non_master(self):
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}):
            result = handle_system_command("restart curie", internal_id="other-user")
        assert result is not None
        assert "restricted" in result.lower() or "master" in result.lower()

    def test_nl_start_allowed_for_master(self):
        with (
            patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}),
            patch(
                "agent.skills.system_commands.start_daemon",
                return_value={
                    "success": True,
                    "pid": 77,
                    "message": "Curie started (PID 77)",
                },
            ),
        ):
            result = handle_system_command("start curie", internal_id="master-user")
        assert result is not None
        assert "77" in result or "started" in result.lower()

    def test_nl_stop_allowed_for_master(self):
        with (
            patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}),
            patch(
                "agent.skills.system_commands.stop_daemon",
                return_value={"success": True, "message": "Curie stopped (PID 77)"},
            ),
        ):
            result = handle_system_command("stop curie", internal_id="master-user")
        assert result is not None
        assert "stopped" in result.lower()
