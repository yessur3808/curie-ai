import os
from pathlib import Path

import pytest

from agent.tooling import policies
from agent.tooling.errors import SandboxCommandError
from agent.tooling.project_tools import _apply_project_change, _code_context

pytestmark = pytest.mark.security


def configure_roots(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    projects = workspace / "projects"
    workspace.mkdir()
    projects.mkdir()
    monkeypatch.setattr(policies, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(policies, "PROJECTS_ROOT", projects)
    return workspace, projects


def test_traversal_symlink_hardlink_secret_and_archive_are_denied(
    monkeypatch, tmp_path
):
    workspace, _ = configure_roots(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (workspace / "bundle.zip").write_bytes(b"not an archive")
    (workspace / "escape").symlink_to(outside)
    os.link(outside, workspace / "hardlink.txt")

    for name in ("../outside.txt", ".env", "bundle.zip", "escape", "hardlink.txt"):
        with pytest.raises((PermissionError, ValueError)):
            policies.safe_path(name)


def test_write_through_in_root_symlink_is_denied(monkeypatch, tmp_path):
    workspace, _ = configure_roots(monkeypatch, tmp_path)
    real = workspace / "real"
    real.mkdir()
    link = workspace / "linked"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PermissionError, match="symbolic"):
        policies.validate_path(
            link / "new.py", root=workspace, write=True, allow_missing=True
        )


def test_project_reads_enforce_file_and_total_size_limits(monkeypatch, tmp_path):
    workspace, _ = configure_roots(monkeypatch, tmp_path)
    (workspace / "one.py").write_text("12345", encoding="utf-8")
    (workspace / "two.py").write_text("67890", encoding="utf-8")
    monkeypatch.setattr(policies, "MAX_TOTAL_BYTES", 6)
    with pytest.raises(ValueError, match="total-byte"):
        list(policies.bounded_files(workspace))


@pytest.mark.parametrize(
    "argv,approved,match",
    [
        (["bash", "-c", "id"], False, "allowlisted"),
        (["python", "-c", "print(1)"], False, "approval"),
        (["pytest", "-q;touch", "bad"], False, "Shell syntax"),
        (["npm", "publish"], False, "approval"),
    ],
)
def test_command_injection_and_approval_bypasses_are_rejected(argv, approved, match):
    with pytest.raises(PermissionError, match=match):
        policies._validate_argv(argv, approved=approved, mutating=False)


def test_safe_read_only_command_profile_accepts_argument_array():
    policies._validate_argv(["pytest", "-q"], approved=False, mutating=False)


def test_sandbox_filters_environment_and_kills_process_group_on_timeout(
    monkeypatch, tmp_path
):
    workspace, _ = configure_roots(monkeypatch, tmp_path)
    captured = {}

    class FakeProcess:
        pid = 4321
        returncode = -9

        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise policies.subprocess.TimeoutExpired("pytest", timeout)
            return b"partial", b"stopped"

    killed = []
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setattr(policies.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(policies.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(
        policies.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    with pytest.raises(TimeoutError, match="exceeded"):
        policies.run_sandboxed(["pytest", "-q"], workspace, timeout=1)

    assert killed == [(4321, policies.signal.SIGKILL)]
    assert captured["shell"] is False
    assert captured["start_new_session"] is True
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "--unshare-all" in captured["argv"]


def test_generated_secret_write_is_rejected_before_any_change(monkeypatch, tmp_path):
    existing = tmp_path / "safe.py"
    existing.write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(
        "llm.manager.ask_llm",
        lambda *args, **kwargs: (
            '{"files":{"safe.py":"changed\\n",".env":"TOKEN=bad\\n"},'
            '"summary":"unsafe"}'
        ),
    )
    with pytest.raises(PermissionError, match="escaped|Secret"):
        _apply_project_change("unsafe", tmp_path)
    assert existing.read_text(encoding="utf-8") == "original\n"


def test_nonzero_sandbox_exit_is_a_typed_safe_failure(monkeypatch, tmp_path):
    workspace, _ = configure_roots(monkeypatch, tmp_path)

    class FakeProcess:
        pid = 4321
        returncode = 1

        def __init__(self, argv, **kwargs):
            pass

        def communicate(self, timeout=None):
            return b"", b"bwrap: Creating new namespace failed: Resource temporarily unavailable"

    monkeypatch.setattr(policies.shutil, "which", lambda _name: "/usr/bin/bwrap")
    monkeypatch.setattr(policies.subprocess, "Popen", FakeProcess)

    with pytest.raises(SandboxCommandError) as caught:
        policies.run_sandboxed(["pytest", "-q"], workspace)
    assert "nothing is still running" in caught.value.user_message.lower()
    assert "bwrap" not in caught.value.user_message.lower()


def test_approved_change_returns_diff_and_retains_rollback(monkeypatch, tmp_path):
    source = tmp_path / "app.py"
    source.write_text("before\n", encoding="utf-8")
    monkeypatch.setattr(
        "llm.manager.ask_llm",
        lambda *args, **kwargs: ('{"files":{"app.py":"after\\n"},"summary":"updated"}'),
    )
    text, changed = _apply_project_change("update", tmp_path)
    backups = list((tmp_path / ".curie-rollbacks").rglob("app.py"))
    assert changed == ["app.py"]
    assert "Patch preview" in text and "-before" in text and "+after" in text
    assert backups and backups[0].read_text(encoding="utf-8") == "before\n"
    assert ".curie-rollbacks" not in _code_context(tmp_path)
