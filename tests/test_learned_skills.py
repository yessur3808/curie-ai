import json

import pytest

from agent.tooling import ToolContext
from memory import adaptive, local_store
from memory.learned_skills import (
    draft_skill,
    handle_skill_command,
    invoke_skill,
)

pytestmark = pytest.mark.security


def _draft(owner="u1", **overrides):
    values = {
        "name": "project_check",
        "trigger_examples": ["check my project"],
        "kind": "executable_workflow",
        "input_schema": {"type": "object", "properties": {}},
        "workflow_steps": [{"tool": "inspect_project", "params": {"path": "."}}],
        "allowed_tools": ["inspect_project"],
        "required_permissions": [],
    }
    values.update(overrides)
    return draft_skill(owner, **values)


def test_draft_is_typed_versioned_and_never_executes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    skill = _draft()
    assert skill["version"] == 1
    assert skill["status"] == "pending"
    assert skill["evaluation_score"] == 0.0
    assert skill["allowed_tools"] == ["inspect_project"]
    assert not (tmp_path / "workspace").exists()


def test_generated_code_paths_and_unlisted_tools_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="Generated code"):
        draft_skill(
            "u1",
            name="bad_recipe",
            trigger_examples=["do unsafe thing"],
            procedure="run this Python: ```python\nprint(1)\n```",
        )
    with pytest.raises(ValueError, match="not explicitly allowed"):
        _draft(
            allowed_tools=[], workflow_steps=[{"tool": "inspect_project", "params": {}}]
        )
    with pytest.raises(ValueError, match="traverse"):
        _draft(
            workflow_steps=[
                {"tool": "inspect_project", "params": {"path": "../secret"}}
            ]
        )


def test_approval_evaluates_and_owner_scope_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    _draft()
    assert adaptive.handle_adaptive_command(
        "u1", "/approve skill project_check"
    ).endswith("approved.")
    skill = adaptive.get_matching_abilities("u1", "please check my project")[0]
    assert skill["evaluation_score"] == 1.0
    assert adaptive.get_matching_abilities("u2", "check my project") == []


@pytest.mark.asyncio
async def test_invocation_rechecks_tool_permissions_and_records_usage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    draft_skill(
        "u1",
        name="make_folder",
        trigger_examples=["make my reports folder"],
        kind="executable_workflow",
        workflow_steps=[{"tool": "create_directory", "params": {"name": "reports"}}],
        allowed_tools=["create_directory"],
        required_permissions=["write_project"],
    )
    adaptive.handle_adaptive_command("u1", "/approve skill make_folder")
    skill = adaptive.get_matching_abilities("u1", "make my reports folder")[0]
    with pytest.raises(PermissionError):
        await invoke_skill(
            skill,
            {},
            ToolContext(internal_id="u1", permissions=frozenset(), approved=False),
        )
    stored = json.loads(handle_skill_command("u1", "/skill export"))["skills"][0]
    assert stored["usage_count"] == 1
    assert stored["failure_count"] == 1


def test_disable_new_version_rollback_feedback_export_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    first = draft_skill(
        "u1",
        name="brief",
        trigger_examples=["give my brief"],
        procedure="summarize priorities",
    )
    adaptive.handle_adaptive_command("u1", "/approve skill brief")
    assert "Disabled" in handle_skill_command("u1", "/skill disable brief")
    second = draft_skill(
        "u1",
        name="brief",
        trigger_examples=["give my brief"],
        procedure="summarize priorities concisely",
    )
    assert (first["version"], second["version"]) == (1, 2)
    adaptive.handle_adaptive_command("u1", "/approve skill brief")
    assert "Recorded" in handle_skill_command("u1", "/skill feedback brief helpful")
    assert "version 1" in handle_skill_command("u1", "/skill rollback brief")
    exported = json.loads(handle_skill_command("u1", "/skill export brief"))
    assert {item["version"] for item in exported["skills"]} == {1, 2}
    assert "Deleted 2" in handle_skill_command("u1", "/skill delete brief")


def test_unused_skill_archives_only_with_explicit_command(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    draft_skill(
        "u1",
        name="unused_recipe",
        trigger_examples=["unused trigger"],
        procedure="give a short response",
    )
    assert "Archived" in handle_skill_command("u1", "/skill archive unused_recipe")
