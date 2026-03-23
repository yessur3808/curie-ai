# tests/test_cli_new_commands.py
"""
Tests for the new CLI subcommands added in the zeroclaw-parity update:
  channel, cron, memory, auth, onboard, completions
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ─── cli.cron ─────────────────────────────────────────────────────────────────

class TestCron:
    """Tests for the cron job registry (no external deps)."""

    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        import cli.cron as cron_mod
        self._mod = cron_mod
        self._orig_curie_dir = cron_mod.CURIE_DIR
        self._orig_cron_file = cron_mod.CRON_FILE
        cron_mod.CURIE_DIR = tmp
        cron_mod.CRON_FILE = tmp / "cron.json"

    def teardown_method(self):
        self._mod.CURIE_DIR = self._orig_curie_dir
        self._mod.CRON_FILE = self._orig_cron_file
        self._tmpdir.cleanup()

    def test_empty_jobs(self):
        assert self._mod.get_jobs() == []

    def test_add_job(self):
        job = self._mod.add_job("*/5 * * * *", "Check system health")
        assert job["schedule"] == "*/5 * * * *"
        assert job["prompt"] == "Check system health"
        assert job["enabled"] is True
        assert "id" in job

    def test_add_multiple_unique_ids(self):
        j1 = self._mod.add_job("@hourly", "Hourly check")
        j2 = self._mod.add_job("@hourly", "Hourly check")
        assert j1["id"] != j2["id"]

    def test_remove_job(self):
        job = self._mod.add_job("@daily", "Daily report")
        removed = self._mod.remove_job(job["id"])
        assert removed is True
        assert self._mod.get_jobs() == []

    def test_remove_nonexistent_returns_false(self):
        assert self._mod.remove_job("does-not-exist") is False

    def test_enable_disable(self):
        job = self._mod.add_job("@monthly", "Monthly cleanup")
        self._mod.set_job_enabled(job["id"], False)
        jobs = self._mod.get_jobs()
        assert jobs[0]["enabled"] is False
        self._mod.set_job_enabled(job["id"], True)
        jobs = self._mod.get_jobs()
        assert jobs[0]["enabled"] is True

    def test_set_enabled_nonexistent_returns_false(self):
        assert self._mod.set_job_enabled("ghost", True) is False

    def test_persistence(self):
        """Jobs survive a second load from disk."""
        self._mod.add_job("*/10 * * * *", "Persistent job")
        jobs = self._mod.get_jobs()
        assert len(jobs) == 1

    def test_cmd_cron_list_empty(self, capsys):
        rc = self._mod.cmd_cron_list()
        assert rc == 0

    def test_cmd_cron_add_and_list(self, capsys):
        rc = self._mod.cmd_cron_add("*/5 * * * *", "Test prompt")
        assert rc == 0
        jobs = self._mod.get_jobs()
        assert len(jobs) == 1

    def test_cmd_cron_remove(self):
        self._mod.add_job("@daily", "to remove")
        jobs = self._mod.get_jobs()
        rc = self._mod.cmd_cron_remove(jobs[0]["id"])
        assert rc == 0
        assert self._mod.get_jobs() == []

    def test_cmd_cron_remove_missing(self):
        rc = self._mod.cmd_cron_remove("no-such-id")
        assert rc == 1

    def test_cmd_cron_enable(self):
        job = self._mod.add_job("@weekly", "weekly task")
        self._mod.set_job_enabled(job["id"], False)
        rc = self._mod.cmd_cron_enable(job["id"], True)
        assert rc == 0

    def test_cmd_cron_enable_missing(self):
        rc = self._mod.cmd_cron_enable("ghost-id", True)
        assert rc == 1


# ─── cli.channel ─────────────────────────────────────────────────────────────

class TestChannel:
    """Tests for channel management (no real network calls)."""

    def test_cmd_channel_list_runs(self):
        from cli.channel import cmd_channel_list
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "fake-token-12345",
            "RUN_TELEGRAM": "true",
            "RUN_API": "true",
        }):
            rc = cmd_channel_list()
        assert rc == 0

    def test_channel_status_telegram_configured(self):
        from cli.channel import _channel_status, _CHANNELS
        ch = next(c for c in _CHANNELS if c["name"] == "telegram")
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok123"}):
            st = _channel_status(ch)
        assert st["configured"] is True

    def test_channel_status_telegram_unconfigured(self):
        from cli.channel import _channel_status, _CHANNELS
        ch = next(c for c in _CHANNELS if c["name"] == "telegram")
        env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            st = _channel_status(ch)
        assert st["configured"] is False

    def test_channel_status_api_always_configured(self):
        from cli.channel import _channel_status, _CHANNELS
        ch = next(c for c in _CHANNELS if c["name"] == "api")
        st = _channel_status(ch)
        assert st["configured"] is True

    def test_cmd_channel_bind_unknown_platform(self, capsys):
        from cli.channel import cmd_channel_bind
        rc = cmd_channel_bind("signal", "some-token")
        assert rc == 1

    def test_cmd_channel_bind_telegram(self, tmp_path):
        from cli.channel import cmd_channel_bind
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch("cli.channel._env_file_path", return_value=env_file):
            rc = cmd_channel_bind("telegram", "new-bot-token-xyz")
        assert rc == 0
        content = env_file.read_text()
        assert "TELEGRAM_BOT_TOKEN=new-bot-token-xyz" in content
        assert "RUN_TELEGRAM=true" in content

    def test_cmd_channel_doctor_runs(self):
        from cli.channel import cmd_channel_doctor
        # Doctor makes network calls; just ensure it runs without raising
        with patch("socket.gethostbyname", side_effect=Exception("no network")):
            rc = cmd_channel_doctor()
        # 0 or 1 depending on connectivity – just must not raise
        assert rc in (0, 1)


# ─── cli.auth ─────────────────────────────────────────────────────────────────

class TestAuth:
    """Tests for LLM provider auth management."""

    def test_cmd_auth_status_runs(self):
        from cli.auth import cmd_auth_status
        with patch.dict(os.environ, {"LLM_PROVIDER_PRIORITY": "llama.cpp"}):
            rc = cmd_auth_status()
        assert rc == 0

    def test_cmd_auth_status_shows_configured_provider(self):
        from cli.auth import cmd_auth_status
        with patch.dict(os.environ, {
            "LLM_PROVIDER_PRIORITY": "openai,llama.cpp",
            "OPENAI_API_KEY": "sk-test-key",
        }):
            rc = cmd_auth_status()
        assert rc == 0

    def test_cmd_auth_login_unknown_provider(self):
        from cli.auth import cmd_auth_login
        rc = cmd_auth_login("unknown-provider")
        assert rc == 1

    def test_cmd_auth_login_local_provider(self):
        from cli.auth import cmd_auth_login
        rc = cmd_auth_login("llama.cpp")
        assert rc == 0

    def test_cmd_auth_login_stores_key(self, tmp_path):
        from cli.auth import cmd_auth_login
        env_file = tmp_path / ".env"
        env_file.write_text("")
        with patch("cli.auth._env_file_path", return_value=env_file), \
             patch.dict(os.environ, {"LLM_PROVIDER_PRIORITY": "llama.cpp"}):
            rc = cmd_auth_login("openai", api_key="sk-fake-key")
        assert rc == 0
        content = env_file.read_text()
        assert "OPENAI_API_KEY=sk-fake-key" in content

    def test_cmd_auth_use_prepends_provider(self, tmp_path):
        from cli.auth import cmd_auth_use
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_PROVIDER_PRIORITY=llama.cpp\n")
        with patch("cli.auth._env_file_path", return_value=env_file), \
             patch.dict(os.environ, {"LLM_PROVIDER_PRIORITY": "llama.cpp"}):
            rc = cmd_auth_use("openai")
        assert rc == 0
        content = env_file.read_text()
        assert "LLM_PROVIDER_PRIORITY=openai,llama.cpp" in content

    def test_cmd_auth_use_unknown_provider_fails(self):
        from cli.auth import cmd_auth_use
        rc = cmd_auth_use("unknown-provider-xyz")
        assert rc == 1


# ─── cli.completions ─────────────────────────────────────────────────────────

class TestCompletions:
    """Tests for shell completion script generation."""

    def test_bash_completions_stdout(self, capsys):
        from cli.completions import cmd_completions
        rc = cmd_completions("bash")
        assert rc == 0
        out = capsys.readouterr().out
        assert "_curie_completions" in out
        assert "complete -F _curie_completions curie" in out

    def test_zsh_completions_stdout(self, capsys):
        from cli.completions import cmd_completions
        rc = cmd_completions("zsh")
        assert rc == 0
        out = capsys.readouterr().out
        assert "_curie" in out
        assert "#compdef curie" in out

    def test_fish_completions_stdout(self, capsys):
        from cli.completions import cmd_completions
        rc = cmd_completions("fish")
        assert rc == 0
        out = capsys.readouterr().out
        assert "complete -c curie" in out

    def test_unknown_shell_fails(self, capsys):
        from cli.completions import cmd_completions
        rc = cmd_completions("powershell")
        assert rc == 1

    def test_completions_contain_all_commands(self, capsys):
        from cli.completions import cmd_completions
        for shell in ("bash", "zsh", "fish"):
            rc = cmd_completions(shell)
            assert rc == 0
            out = capsys.readouterr().out
            for cmd in ("start", "stop", "status", "channel", "cron", "memory", "auth", "completions"):
                assert cmd in out, f"'{cmd}' missing from {shell} completions"


# ─── cli.main new subcommands ─────────────────────────────────────────────────

class TestCLIMainNewCommands:
    """Test that new subcommands are wired into cli.main._build_parser."""

    def _parse(self, argv):
        from cli.main import _build_parser
        return _build_parser().parse_args(argv)

    def test_onboard_command(self):
        args = self._parse(["onboard"])
        assert args.command == "onboard"

    def test_onboard_verbose(self):
        args = self._parse(["onboard", "--verbose"])
        assert args.verbose is True

    def test_channel_list(self):
        args = self._parse(["channel", "list"])
        assert args.command == "channel"
        assert args.channel_action == "list"

    def test_channel_doctor(self):
        args = self._parse(["channel", "doctor"])
        assert args.channel_action == "doctor"

    def test_channel_bind_telegram(self):
        args = self._parse(["channel", "bind-telegram", "bot-token-xyz"])
        assert args.channel_action == "bind-telegram"
        assert args.token == "bot-token-xyz"

    def test_cron_list(self):
        args = self._parse(["cron", "list"])
        assert args.command == "cron"
        assert args.cron_action == "list"

    def test_cron_add(self):
        args = self._parse(["cron", "add", "*/5 * * * *", "--prompt", "check health"])
        assert args.cron_action == "add"
        assert args.schedule == "*/5 * * * *"
        assert args.prompt == "check health"

    def test_cron_remove(self):
        args = self._parse(["cron", "remove", "job-id-123"])
        assert args.cron_action == "remove"
        assert args.job_id == "job-id-123"

    def test_cron_enable(self):
        args = self._parse(["cron", "enable", "job-id-abc"])
        assert args.cron_action == "enable"
        assert args.job_id == "job-id-abc"

    def test_cron_disable(self):
        args = self._parse(["cron", "disable", "job-id-abc"])
        assert args.cron_action == "disable"

    def test_memory_list(self):
        args = self._parse(["memory", "list"])
        assert args.command == "memory"
        assert args.memory_action == "list"

    def test_memory_list_limit(self):
        args = self._parse(["memory", "list", "--limit", "5"])
        assert args.limit == 5

    def test_memory_get(self):
        args = self._parse(["memory", "get", "hobby"])
        assert args.memory_action == "get"
        assert args.key == "hobby"

    def test_memory_get_with_user(self):
        args = self._parse(["memory", "get", "hobby", "--user", "uid-123"])
        assert args.user == "uid-123"

    def test_memory_stats(self):
        args = self._parse(["memory", "stats"])
        assert args.memory_action == "stats"

    def test_memory_clear_user(self):
        args = self._parse(["memory", "clear-user", "uid-abc"])
        assert args.memory_action == "clear-user"
        assert args.user_id == "uid-abc"

    def test_auth_login(self):
        args = self._parse(["auth", "login", "--provider", "openai"])
        assert args.command == "auth"
        assert args.auth_action == "login"
        assert args.provider == "openai"

    def test_auth_status(self):
        args = self._parse(["auth", "status"])
        assert args.auth_action == "status"

    def test_auth_use(self):
        args = self._parse(["auth", "use", "--provider", "anthropic"])
        assert args.auth_action == "use"
        assert args.provider == "anthropic"

    def test_completions_bash(self):
        args = self._parse(["completions", "bash"])
        assert args.command == "completions"
        assert args.shell == "bash"

    def test_completions_zsh(self):
        args = self._parse(["completions", "zsh"])
        assert args.shell == "zsh"

    def test_completions_fish(self):
        args = self._parse(["completions", "fish"])
        assert args.shell == "fish"

    def test_completions_via_main(self, capsys):
        from cli.main import main
        rc = main(["completions", "bash"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "complete -F _curie_completions curie" in out

    def test_channel_list_via_main(self):
        from cli.main import main
        with patch("cli.channel.cmd_channel_list", return_value=0) as mock_list:
            rc = main(["channel", "list"])
        assert rc == 0
        mock_list.assert_called_once()

    def test_cron_list_via_main(self):
        from cli.main import main
        with patch("cli.cron.cmd_cron_list", return_value=0) as mock_list:
            rc = main(["cron", "list"])
        assert rc == 0
        mock_list.assert_called_once()

    def test_auth_status_via_main(self):
        from cli.main import main
        with patch("cli.auth.cmd_auth_status", return_value=0) as mock_status:
            rc = main(["auth", "status"])
        assert rc == 0
        mock_status.assert_called_once()


# ─── cli.onboard helpers ─────────────────────────────────────────────────────

class TestOnboard:
    """Tests for onboard helper functions (no interactive prompts)."""

    def test_load_env_file_empty(self, tmp_path):
        from cli.onboard import _load_env_file
        result = _load_env_file(tmp_path / "nonexistent.env")
        assert result == {}

    def test_load_env_file_parses_keys(self, tmp_path):
        from cli.onboard import _load_env_file
        env = tmp_path / ".env"
        env.write_text("POSTGRES_DSN=postgresql://localhost/curie\nMONGODB_URI=mongodb://localhost\n")
        result = _load_env_file(env)
        assert result["POSTGRES_DSN"] == "postgresql://localhost/curie"
        assert result["MONGODB_URI"] == "mongodb://localhost"

    def test_write_env_file_new_key(self, tmp_path):
        from cli.onboard import _write_env_file
        env = tmp_path / ".env"
        env.write_text("EXISTING=value\n")
        _write_env_file(env, {"NEW_KEY": "new_value"})
        content = env.read_text()
        assert "NEW_KEY=new_value" in content
        assert "EXISTING=value" in content

    def test_write_env_file_updates_existing_key(self, tmp_path):
        from cli.onboard import _write_env_file
        env = tmp_path / ".env"
        env.write_text("MY_KEY=old_value\nOTHER=keep\n")
        _write_env_file(env, {"MY_KEY": "new_value"})
        content = env.read_text()
        assert "MY_KEY=new_value" in content
        assert "old_value" not in content
        assert "OTHER=keep" in content

    def test_write_env_file_creates_if_missing(self, tmp_path):
        from cli.onboard import _write_env_file
        env = tmp_path / "new.env"
        _write_env_file(env, {"FOO": "bar"})
        assert "FOO=bar" in env.read_text()


# ─── system_commands.py new patterns ─────────────────────────────────────────

class TestSystemCommandsNewPatterns:
    """Test that new NL patterns are detected correctly."""

    def test_slash_channel(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("/channel") == "channel"

    def test_slash_cron(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("/cron") == "cron"

    def test_slash_memory(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("/memory") == "memory"

    def test_slash_auth(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("/auth") == "auth"

    def test_nl_list_channels(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("list channels") == "channel"

    def test_nl_which_channels_configured(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("which channels are configured?") == "channel"

    def test_nl_show_cron_jobs(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("show scheduled jobs") == "cron"

    def test_nl_cron_list(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("cron list") == "cron"

    def test_nl_show_memory_facts(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("show user memory") == "memory"

    def test_nl_stored_facts(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("show stored facts") == "memory"

    def test_nl_which_llm_provider(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("which llm provider is active?") == "auth"

    def test_nl_auth_status(self):
        from agent.skills.system_commands import detect_system_command
        assert detect_system_command("auth status") == "auth"

    def test_channel_command_returns_channel_list(self):
        from agent.skills.system_commands import handle_system_command
        with patch("agent.skills.system_commands._render_channel_list", return_value="chan list"):
            result = handle_system_command("/channel", internal_id="u1")
        assert result == "chan list"

    def test_cron_command_returns_cron_list(self):
        from agent.skills.system_commands import handle_system_command
        with patch("agent.skills.system_commands._render_cron_list", return_value="cron list"):
            result = handle_system_command("/cron", internal_id="u1")
        assert result == "cron list"

    def test_auth_command_blocked_for_non_master(self):
        from agent.skills.system_commands import handle_system_command
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}):
            result = handle_system_command("/auth", internal_id="non-master")
        assert result is not None
        assert "restricted" in result.lower() or "master" in result.lower()

    def test_auth_command_allowed_for_master(self):
        from agent.skills.system_commands import handle_system_command
        with patch.dict(os.environ, {"MASTER_USER_ID": "master-user"}), \
             patch("agent.skills.system_commands._render_auth_status", return_value="auth ok"):
            result = handle_system_command("/auth", internal_id="master-user")
        assert result == "auth ok"
