import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
import agent.action_router as router
from agent.tooling import policies
import memory.local_store as local_store

pytestmark = [pytest.mark.integration, pytest.mark.security]


def test_active_chat_path_uses_guarded_action_router(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    monkeypatch.setattr(policies, "WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(policies, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "workspace").mkdir()
    monkeypatch.delenv("MASTER_USER_ID", raising=False)

    workflow = ChatWorkflow(
        persona={"name": "Curie", "system_prompt": "You are Curie."}
    )
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "test",
                "external_user_id": "user-1",
                "external_chat_id": "chat-1",
                "internal_id": "internal-1",
                "message_id": "message-1",
                "text": "Create a new Python project called weather-dashboard",
            }
        )
    )

    assert result["model_used"] == "action_router:create_python_project"
    assert (
        tmp_path / "projects" / "internal-1" / "weather-dashboard" / "pyproject.toml"
    ).exists()
