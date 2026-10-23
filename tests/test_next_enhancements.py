from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_resource_governor_enforces_budget_and_downgrades(monkeypatch, tmp_path):
    monkeypatch.setenv("CURIE_CONVERSATION_ALLOW_MODEL_DOWNGRADE", "true")
    from services.resource_governor import ResourceGovernor

    monkeypatch.setenv("CURIE_BUDGET_OPEN_SOURCE_PLANNING_TOKENS_DAY", "1000")
    governor = ResourceGovernor(tmp_path / "budgets.json")
    governor.record("open_source_planning", "owner", tokens=600, wall_seconds=1)
    decision = governor.admit(
        "open_source_planning",
        "owner",
        800,
        preferred_model="large",
        fallback_model="small",
    )
    assert decision.allowed
    assert decision.downgraded
    assert decision.model == "small"
    assert decision.max_tokens <= 400


def test_diagnostic_trends_are_dashboard_ready(tmp_path):
    from services.self_diagnostics import DiagnosticMonitor

    monitor = DiagnosticMonitor(tmp_path / "diagnostics.json")
    ticket = monitor.start(owner_id="owner", connector="telegram")
    monitor.finish(
        ticket,
        {
            "text": "done",
            "model_used": "test-model",
            "telemetry": {"output_tokens": 4},
        },
    )
    trends = monitor.trends(owner_id="owner", hours=1, bucket_minutes=5)
    assert trends["points"]
    assert trends["points"][0]["commands"] == 1
    assert trends["points"][0]["tokens"] >= 4
    assert trends["mode"] in {"normal", "constrained", "paused"}


@pytest.mark.asyncio
async def test_external_message_preview_is_exact_and_immutable(monkeypatch, tmp_path):
    import services.action_previews as previews
    from agent.intent_router import ToolRequest

    store = previews.ActionPreviewStore(tmp_path / "previews")
    monkeypatch.setattr(previews, "preview_store", store)
    request, rendered = await previews.prepare_action_preview(
        ToolRequest(
            "gmail_send",
            {
                "recipient": "friend@example.com",
                "subject": "Hello",
                "body": "Exact body",
            },
            needs_approval=True,
        ),
        "owner",
        {},
    )
    assert "friend@example.com" in rendered
    assert "Exact body" in rendered
    restored, preview = previews.materialize_request_preview(
        "owner", "gmail_send", request.params
    )
    assert restored["body"] == "Exact body"
    tampered = dict(request.params)
    tampered["preview_hash"] = "0" * 64
    with pytest.raises(PermissionError):
        previews.materialize_request_preview("owner", "gmail_send", tampered)
    previews.consume_materialized_preview("owner", preview)
    with pytest.raises((ValueError, PermissionError)):
        previews.materialize_request_preview("owner", "gmail_send", request.params)


def test_home_batches_and_reminders_require_preview():
    from agent.intent_router import classify_request

    single = classify_request("turn off the floor lamp")
    batch = classify_request("turn off the floor lamp and TV light")
    group = classify_request("turn off all lights")
    reminder = classify_request("remind me in 30 minutes to call mom")
    assert single and single.action == "home_control" and not single.needs_approval
    assert batch and batch.action == "home_control" and batch.needs_approval
    assert group and group.action == "home_control" and group.needs_approval
    assert reminder and reminder.action == "reminder_create" and reminder.needs_approval
    assert "call mom" in reminder.params["message"]


def test_calendar_scheduling_is_not_misrouted_as_a_reminder(monkeypatch):
    from agent.intent_router import classify_request

    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    request = classify_request("Schedule a team meeting tomorrow at 3pm on my calendar")
    assert request and request.action == "calendar_create"
    assert request.needs_approval


def test_github_app_is_preferred_without_exposing_key(monkeypatch, tmp_path):
    from services.github_auth import GitHubCredentialBroker

    key = tmp_path / "github-app.pem"
    key.write_text("not-a-real-key", encoding="utf-8")
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("GITHUB_TOKEN", "compatibility-token")
    status = GitHubCredentialBroker().status()
    assert status.mode == "github_app"
    assert status.write_ready
    assert "not-a-real-key" not in status.detail


def test_contribution_security_scan_fails_closed():
    from services.contribution_sandbox import ContributionSandbox

    unsafe_assignment = "pass" + 'word = "' + ("x" * 16) + '"'
    result = ContributionSandbox._security_scan(
        [{"path": "tool.py", "after": unsafe_assignment}]
    )
    assert result.status == "failed"


def test_contribution_learning_changes_repository_score(tmp_path):
    from services.contribution_learning import ContributionLearningStore

    store = ContributionLearningStore(tmp_path / "learning")
    store.record("owner", "preview_rejected", repository="org/noisy", topic="python")
    store.record("owner", "pull_request_merged", repository="org/good", topic="python")
    assert store.score("owner", "org/good", "python") > store.score(
        "owner", "org/noisy", "python"
    )


def test_sandbox_runtime_command_is_hardened(monkeypatch, tmp_path):
    from services import contribution_sandbox as module

    calls = []

    def fake_run(argv, *, cwd=None, timeout=120):
        calls.append(list(argv))
        if argv[:2] == ["git", "-c"]:
            root = Path(argv[-1])
            root.mkdir(parents=True)
            (root / "example.py").write_text("value = 1\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="")
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="abc\n")
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr(module.shutil, "which", lambda value: f"/usr/bin/{value}")
    result = module.ContributionSandbox().verify(
        {
            "repository_url": "https://github.com/org/repo",
            "base_branch": "main",
            "base_sha": "abc",
            "files": [
                {
                    "path": "example.py",
                    "before": "value = 1\n",
                    "after": "value = 2\n",
                    "before_hash": hashlib.sha256(b"value = 1\n").hexdigest(),
                }
            ],
        }
    )
    assert result["passed"]
    docker_calls = [call for call in calls if call[:2] == ["docker", "run"]]
    docker = docker_calls[0]
    assert "--network=none" in docker
    assert "--cap-drop=ALL" in docker
    assert "--read-only" in docker
    assert any("ruff" in call for call in docker_calls)
    assert any("bandit" in call for call in docker_calls)
    assert any("pytest" in call for call in docker_calls)
