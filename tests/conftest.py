"""Global offline-test isolation and lifecycle boundaries."""

from __future__ import annotations

# Import real application packages before test-module collection. This prevents
# optional-dependency shims from replacing whole Curie packages in sys.modules.
import llm as _real_llm  # noqa: F401
import memory as _real_memory  # noqa: F401

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    """Give every test isolated persistence and clean process-global caches."""
    from agent.tooling import policies
    from memory import local_store
    from memory.repositories import reset_repositories
    from memory.session_store import reset_session_manager
    from utils import conversions, weather
    from agent.observability import reset_telemetry

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    workspace = tmp_path / "workspace"
    projects = tmp_path / "projects"
    monkeypatch.setattr(policies, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(policies, "PROJECTS_ROOT", projects)
    reset_session_manager()
    reset_repositories()
    conversions.reset_cache()
    weather.reset_cache()
    reset_telemetry(path=tmp_path / "telemetry.json")

    try:
        from llm.manager import reset_runtime_state

        reset_runtime_state(unload_models=False)
    except ImportError:
        pass
    try:
        from llm.inference_service import reset_inference_service

        reset_inference_service()
    except ImportError:
        pass
    try:
        from agent.task_runtime import reset_task_runtime

        reset_task_runtime()
    except ImportError:
        pass
    try:
        from llm.inference_service import reset_inference_service

        reset_inference_service()
    except ImportError:
        pass
    yield
    reset_session_manager()
    reset_repositories()
    conversions.reset_cache()
    weather.reset_cache()
    reset_telemetry(path=tmp_path / "telemetry.json")
    try:
        from agent.task_runtime import reset_task_runtime

        reset_task_runtime()
    except ImportError:
        pass
