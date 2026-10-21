from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from agent.action_router import execute_request
from agent.intent_router import ToolRequest, classify_request
from agent.tooling import policies
from services.action_previews import preview_store
from services.open_source_contributions import OpenSourceContributionService
from services.self_diagnostics import DiagnosticMonitor, render_diagnostics


def _approval_token(text: str) -> str:
    return text.split("/approve action ", 1)[1][:8]


def test_project_change_is_previewed_before_any_write(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    root = projects / "owner"
    root.mkdir(parents=True)
    source = root / "app.py"
    source.write_text("before = True\n", encoding="utf-8")
    monkeypatch.setattr(policies, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(preview_store, "root", tmp_path / "previews")
    monkeypatch.setattr(
        "llm.manager.ask_llm",
        lambda *args, **kwargs: '{"files":{"app.py":"after = True\\n"},"summary":"Small fix."}',
    )

    request = ToolRequest(
        "project_change",
        {"request": "make the small fix", "path": ".", "run_tests": False},
        needs_approval=True,
        explanation="apply a small code fix",
    )
    preview = asyncio.run(execute_request(request, "owner", {}))

    assert "Exact changes" in preview
    assert "-before = True" in preview
    assert "+after = True" in preview
    assert source.read_text(encoding="utf-8") == "before = True\n"

    result = asyncio.run(
        execute_request(
            ToolRequest("approve", {"token": _approval_token(preview)}),
            "owner",
            {},
        )
    )
    assert "Applied the approved change" in result
    assert source.read_text(encoding="utf-8") == "after = True\n"


def test_stale_file_blocks_approved_preview(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    root = projects / "owner"
    root.mkdir(parents=True)
    source = root / "app.py"
    source.write_text("before\n", encoding="utf-8")
    monkeypatch.setattr(policies, "PROJECTS_ROOT", projects)
    monkeypatch.setattr(preview_store, "root", tmp_path / "previews")
    monkeypatch.setattr(
        "llm.manager.ask_llm",
        lambda *args, **kwargs: '{"files":{"app.py":"approved\\n"},"summary":"Fix."}',
    )
    preview = asyncio.run(
        execute_request(
            ToolRequest(
                "project_change",
                {"request": "fix", "path": ".", "run_tests": False},
                needs_approval=True,
                explanation="apply the fix",
            ),
            "owner",
            {},
        )
    )
    source.write_text("newer user work\n", encoding="utf-8")
    result = asyncio.run(
        execute_request(
            ToolRequest("approve", {"token": _approval_token(preview)}),
            "owner",
            {},
        )
    )
    assert "changed after the preview" in result
    assert source.read_text(encoding="utf-8") == "newer user work\n"


def test_natural_preview_commands_are_classified():
    organize = classify_request(
        "Organize selected files report.txt and photo.jpg into folder archive"
    )
    assert organize.action == "organize_files"
    assert organize.needs_approval is True
    note = classify_request("Draft a note named notes/today.md: Remember the meeting")
    assert note.action == "draft_note"
    assert note.params["content"] == "Remember the meeting"


def test_diagnostics_capture_resources_tokens_and_summary(tmp_path):
    monitor = DiagnosticMonitor(path=tmp_path / "diagnostics.json")
    ticket = monitor.start(owner_id="owner", connector="telegram")
    record = monitor.finish(
        ticket,
        {
            "text": "A concise answer",
            "model_used": "test-model",
            "timings_ms": {"model_response": 12.5},
            "telemetry": {
                "prompt_tokens_estimate": 20,
                "output_tokens": 7,
                "queue_wait_ms": 2,
                "first_token_ms": 5,
            },
        },
    )
    assert record["latency_ms"] >= 0
    assert record["cpu_ms"] >= 0
    assert record["rss_end_mb"] > 0
    assert record["prompt_tokens_estimate"] == 20
    assert monitor.snapshot(owner_id="owner")["count"] == 1


def test_two_role_github_workflow_stops_at_preview(monkeypatch, tmp_path):
    class Content:
        size = 12
        decoded_content = b"value = 1\n"

    class Repo:
        full_name = "acme/demo"
        default_branch = "main"
        html_url = "https://github.com/acme/demo"
        archived = False
        fork = False
        license = SimpleNamespace(key="mit")

        def get_branch(self, name):
            return SimpleNamespace(commit=SimpleNamespace(sha="base-sha"))

        def get_git_tree(self, ref, recursive=True):
            return SimpleNamespace(tree=[SimpleNamespace(type="blob", path="app.py")])

        def get_contents(self, path, ref=None):
            assert path == "app.py"
            return Content()

    issue = SimpleNamespace(
        repository_url="https://api.github.com/repos/acme/demo",
        number=7,
        title="Clarify the default value",
        body="Please clarify this small behavior.",
        html_url="https://github.com/acme/demo/issues/7",
    )
    repo = Repo()

    class Client:
        def search_issues(self, **kwargs):
            return [issue]

        def get_repo(self, name):
            assert name == "acme/demo"
            return repo

    calls = []

    def fake_model(prompt, **kwargs):
        calls.append(kwargs["role"])
        if kwargs["role"] == "reasoning":
            return '{"objective":"Clarify value","files":["app.py"],"tests":["syntax"],"title":"Clarify default value","body":"Small clarification."}'
        return '{"files":{"app.py":"value = 2\\n"},"summary":"Updated the default."}'

    monkeypatch.setattr("llm.manager.ask_llm", fake_model)
    monkeypatch.setattr(preview_store, "root", tmp_path / "previews")
    monkeypatch.setattr(
        "services.contribution_sandbox.contribution_sandbox.verify",
        lambda _payload: {
            "passed": True,
            "network": "disabled",
            "checks": [
                {
                    "name": "project_tests",
                    "status": "passed",
                    "detail": "unit-test sandbox boundary",
                }
            ],
        },
    )
    monkeypatch.setenv("CURIE_OPEN_SOURCE_REASONING_MODEL", "planner.gguf")
    monkeypatch.setenv("CURIE_OPEN_SOURCE_CODING_MODEL", "coder.gguf")
    service = OpenSourceContributionService(github_client=Client())

    preview = service.prepare("owner", {}, topic="python")

    assert calls == ["reasoning", "coding"]
    assert preview["action"] == "github_contribution_publish"
    assert preview["payload"]["planner_model"] == "planner.gguf"
    assert preview["payload"]["coder_model"] == "coder.gguf"
    assert "+value = 2" in preview["exact_changes"]
    assert not any(tmp_path.rglob(".git"))
