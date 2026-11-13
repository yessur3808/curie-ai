from __future__ import annotations

import asyncio
from difflib import unified_diff
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.action_previews import ActionPreviewStore
from services.bounded_autonomy import (
    BoundedAutonomyService,
    _safe_relative_path,
    get_report,
    handle_bounded_autonomy_command,
    render_digest,
)
from services.proactive_messaging import ProactiveMessagingService


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, *, passing: bool) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "app.py").write_text(
        "def value():\n    return 1\n" if passing else "def value():\n    return 0\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_sample.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "curie-tests@example.invalid")
    _git(root, "config", "user.name", "Curie Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    return root


def _repair_proposal(_request: str, worktree: Path) -> dict:
    path = worktree / "app.py"
    before = path.read_text(encoding="utf-8")
    after = "def value():\n    return 1\n"
    diff = "\n".join(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="a/app.py",
            tofile="b/app.py",
            lineterm="",
        )
    )
    return {
        "root": str(worktree),
        "request": "Repair the failing value test",
        "summary": "Return the expected value.",
        "files": [
            {
                "path": "app.py",
                "existed": True,
                "before": before,
                "after": after,
                "before_hash": hashlib.sha256(before.encode()).hexdigest(),
            }
        ],
        "diff": f"```diff\n{diff}\n```",
    }


def _service(tmp_path: Path, root: Path, *, proposer=None) -> BoundedAutonomyService:
    return BoundedAutonomyService(
        root,
        state_root=tmp_path / "state",
        previews=ActionPreviewStore(tmp_path / "previews"),
        proposer=proposer,
        python_executable=Path(sys.executable),
        test_targets=("tests/test_sample.py",),
    )


def test_virtualenv_launcher_path_is_not_resolved_to_system_python(tmp_path):
    root = _repo(tmp_path, passing=True)
    launcher = tmp_path / "venv-python"
    launcher.symlink_to(Path(sys.executable))

    service = BoundedAutonomyService(
        root,
        state_root=tmp_path / "state",
        previews=ActionPreviewStore(tmp_path / "previews"),
        python_executable=launcher,
        test_targets=("tests/test_sample.py",),
    )

    assert service.python_executable == launcher.absolute()
    assert service.python_executable != launcher.resolve()


def test_clean_cycle_is_read_only_and_records_evidence(tmp_path):
    from memory import UserManager

    root = _repo(tmp_path, passing=True)
    service = _service(tmp_path, root)
    UserManager.update_user_profile(
        "owner", {"bounded_autonomy_pending_report_id": "older-alert"}
    )

    report = service.run_cycle("owner", {"bounded_autonomy_auto_prepare": True})

    assert report["status"] == "healthy"
    assert report["actionable"] is False
    assert report["preview_id"] == ""
    assert _git(root, "status", "--porcelain") == ""
    assert get_report("owner", report["id"])["base_sha"] == _git(
        root, "rev-parse", "HEAD"
    )
    assert (
        UserManager.get_user_profile("owner")["bounded_autonomy_pending_report_id"]
        == ""
    )
    assert not list((tmp_path / "state" / "worktrees").iterdir())


def test_failing_cycle_prepares_verified_immutable_preview(tmp_path):
    root = _repo(tmp_path, passing=False)
    service = _service(tmp_path, root, proposer=_repair_proposal)

    report = service.run_cycle("owner", {"bounded_autonomy_auto_prepare": True})

    assert report["status"] == "patch_ready"
    assert report["actionable"] is True
    preview = service.previews.load(
        "owner", report["preview_id"], expected_action="project_change"
    )
    assert preview["payload"]["root"] == str(root)
    assert preview["payload"]["files"][0]["after"].endswith("return 1\n")
    assert (root / "app.py").read_text(encoding="utf-8").endswith("return 0\n")
    assert "Nothing has been applied" in render_digest(report)


def test_live_source_race_blocks_preview_and_preserves_dirty_file(tmp_path):
    root = _repo(tmp_path, passing=False)
    (root / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    service = _service(tmp_path, root, proposer=_repair_proposal)

    report = service.run_cycle("owner", {"bounded_autonomy_auto_prepare": True})

    assert report["status"] == "needs_attention"
    assert report["preview_id"] == ""
    assert "differs from the isolated base" in report["summary"]
    assert (root / "app.py").read_text(encoding="utf-8").endswith("return 2\n")


@pytest.mark.parametrize(
    "path",
    [
        "deploy/ecosystem.config.js",
        "requirements.txt",
        "agent/action_router.py",
        "services/bounded_autonomy.py",
        "../escape.py",
    ],
)
def test_autonomous_repair_cannot_change_governance_or_dependencies(path):
    with pytest.raises(PermissionError):
        _safe_relative_path(path)


def test_live_registry_advertises_the_bounded_authority_contract():
    from agent.tooling import get_runtime_registry, reset_runtime_registry

    reset_runtime_registry()
    capability = get_runtime_registry().get("bounded_autonomy")

    assert capability.availability()[0] is True
    assert capability.risk == "read_only"
    assert capability.chat_routable is False
    assert "fresh owner approval" in capability.description
    assert "never autonomous" in capability.description


def test_owner_commands_enable_and_bound_the_loop(monkeypatch):
    monkeypatch.setenv("MASTER_USER_ID", "owner")

    enabled = asyncio.run(handle_bounded_autonomy_command("owner", "/autonomy on", {}))
    status = asyncio.run(
        handle_bounded_autonomy_command("owner", "/autonomy status", {})
    )
    denied = asyncio.run(
        handle_bounded_autonomy_command("someone-else", "/autonomy on", {})
    )

    assert "Bounded autonomy is on" in enabled
    assert "Never autonomous" in status
    assert "configured owner" in denied


@pytest.mark.asyncio
async def test_review_command_uses_existing_expiring_action_approval(monkeypatch):
    from agent.action_router import execute_request
    from agent.intent_router import ToolRequest
    from agent.tooling import policies
    from services.action_previews import preview_store

    monkeypatch.setenv("MASTER_USER_ID", "owner")
    policies.WORKSPACE_ROOT.mkdir(parents=True)
    preview = preview_store.create(
        "owner",
        action="project_change",
        payload={
            "root": str(policies.WORKSPACE_ROOT),
            "request": "fixture",
            "summary": "A tested fixture patch.",
            "files": [],
            "diff": "```diff\n+safe\n```",
            "run_tests": False,
        },
        summary="A tested fixture patch.",
        exact_changes="```diff\n+safe\n```",
    )

    response = await handle_bounded_autonomy_command(
        "owner",
        f"/autonomy review {preview['id']}",
        {"_connector": "telegram"},
    )

    assert "Preview — Project Change" in response
    assert "/approve action" in response
    assert "apply exactly this preview" in response
    token = re.search(r"/approve action ([A-Za-z0-9_-]+)", response).group(1)
    receipt = await execute_request(
        ToolRequest("approve", {"token": token}),
        "owner",
        {"_connector": "telegram"},
    )
    assert "invalid, expired" not in receipt
    assert "Applied the approved change" in receipt


@pytest.mark.asyncio
async def test_actionable_report_uses_existing_proactive_delivery_policy():
    from memory.local_store import save_personal_item

    saved = save_personal_item(
        "owner",
        "bounded_autonomy_report",
        {
            "id": "report-source",
            "status": "needs_attention",
            "actionable": True,
            "summary": "The isolated regression lane needs attention.",
            "preview_id": "",
        },
    )
    calls = []

    async def connector(user, message):
        calls.append((user, message))
        return True

    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": connector}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_interval_hours": 0,
        "proactive_quiet_hours": {"start": 0, "end": 0},
        "proactive_daily_max": 2,
        "timezone": "UTC",
        "bounded_autonomy_pending_report_id": saved["id"],
    }
    sessions = MagicMock()
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile") as update,
        patch(
            "services.proactive_messaging.get_session_manager",
            return_value=sessions,
        ),
        patch("services.proactive_messaging.random.random", return_value=0.0),
    ):
        await service._maybe_send_proactive_message(
            {
                "internal_id": "owner",
                "platform": "telegram",
                "external_user_id": "42",
            }
        )

    assert len(calls) == 1
    assert "Curie maintenance" in calls[0][1]
    stored = update.call_args.args[1]
    assert stored["bounded_autonomy_pending_report_id"] == ""
