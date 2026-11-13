"""Global offline-test isolation and lifecycle boundaries."""

from __future__ import annotations

# Import real application packages before test-module collection. This prevents
# optional-dependency shims from replacing whole Curie packages in sys.modules.
import llm as _real_llm  # noqa: F401
import memory as _real_memory  # noqa: F401

import pytest

# Offline jobs stub the optional Telegram SDK in connector test modules. Keep
# real exception types (not MagicMocks), so transport retry tests exercise the
# same catch boundaries without requiring a live bot SDK in lightweight CI.
try:
    import telegram.error  # noqa: F401
except ModuleNotFoundError:
    import sys
    from types import ModuleType

    errors = ModuleType("telegram.error")
    errors.BadRequest = type("BadRequest", (Exception,), {})
    errors.NetworkError = type("NetworkError", (Exception,), {})
    sys.modules["telegram.error"] = errors


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    """Give every test isolated persistence and clean process-global caches."""
    from agent.tooling import policies
    from memory import local_store
    from memory.repositories import reset_repositories
    from memory.session_store import reset_session_manager
    from utils import conversions, weather
    from agent.observability import reset_telemetry
    from services.action_previews import preview_store
    from services.contribution_learning import contribution_learning
    from services.resource_governor import resource_governor
    from services.self_diagnostics import diagnostic_monitor
    from collections import deque

    # These singleton services can be imported before the fixture runs.
    # Isolate their objects as well as environment defaults so repeated offline
    # suites neither consume live budgets nor inherit persisted test usage.
    monkeypatch.setenv("CURIE_RESOURCE_BUDGET_PATH", str(tmp_path / "budgets.json"))
    monkeypatch.setenv("CURIE_DIAGNOSTICS_PATH", str(tmp_path / "diagnostics.json"))
    monkeypatch.setattr(resource_governor, "path", tmp_path / "budgets.json")
    monkeypatch.setattr(resource_governor, "_events", [])
    monkeypatch.setattr(diagnostic_monitor, "path", tmp_path / "diagnostics.json")
    monkeypatch.setattr(diagnostic_monitor, "_records", deque(maxlen=250))

    # Preview and contribution tests must never touch the operator's live state.
    monkeypatch.setattr(preview_store, "root", tmp_path / "action-previews")
    monkeypatch.setattr(
        contribution_learning, "root", tmp_path / "contribution-learning"
    )

    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("MONGODB_URI", raising=False)
    # The production circuit breaker defaults to ~/.curie so it survives
    # process restarts. Tests must never read from or write to that live state:
    # a deliberately secret-shaped fixture once tripped the real pipeline and
    # silently forced every subsequent production turn onto the legacy path.
    monkeypatch.setenv(
        "CURIE_PIPELINE_ROLLOUT_STATE", str(tmp_path / "pipeline-rollout.json")
    )
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
