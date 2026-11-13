import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import letta_specialists as specialists
from agent.tooling.letta_tools import LettaSpecialistTool
from agent.tooling.contracts import ToolContext


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    config = {
        "enabled": True,
        "owner": "owner",
        "agents": {k: "agent-local-fixture-" + k for k in specialists.SPECIALTIES},
    }
    path = tmp_path / "specialists.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("CURIE_LETTA_SPECIALISTS_CONFIG", str(path))
    monkeypatch.setattr(specialists.Path, "home", lambda: tmp_path)
    from services import resource_governor as rg

    monkeypatch.setattr(
        rg, "resource_governor", rg.ResourceGovernor(tmp_path / "budget.json")
    )
    monkeypatch.setattr(
        "services.self_diagnostics.diagnostic_monitor.snapshot", lambda **kw: {}
    )
    return config


@pytest.mark.asyncio
async def test_owner_boundary_rejects_before_model_call(enabled, monkeypatch):
    runner = AsyncMock()
    monkeypatch.setattr(specialists, "run_local", runner)
    result = await specialists.review(
        "someone-else", "workflow", "Compare agent workflows"
    )
    assert result.status == "owner_not_enabled"
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_advice_redacts_input_and_output_and_disables_tools(enabled, monkeypatch):
    runner = AsyncMock(
        return_value=specialists.ReviewResult(
            "completed", "Check authentication; api_key=example-private-value"
        )
    )
    monkeypatch.setattr(specialists, "run_local", runner)
    result = await LettaSpecialistTool("operations").execute(
        {
            "text": "Diagnose service failure. Authorization: Bearer example-private-value"
        },
        ToolContext("owner"),
    )
    argv, prompt = runner.call_args.args
    assert argv[argv.index("--toolset") + 1] == "none"
    assert "example-private-value" not in prompt
    assert "example-private-value" not in result.text
    assert result.data["executed_actions"] is False
    assert result.data["status"] == "completed"


@pytest.mark.asyncio
async def test_budget_limits_specialist_calls(enabled, monkeypatch):
    runner = AsyncMock(return_value=specialists.ReviewResult("completed", "Advice"))
    monkeypatch.setattr(specialists, "run_local", runner)
    outcomes = [
        await specialists.review("owner", "engineering", "Review this test plan")
        for _ in range(5)
    ]
    assert outcomes[-1].status == "resource_deferred"
    assert runner.await_count == 4


@pytest.mark.asyncio
async def test_private_key_refused(enabled, monkeypatch):
    runner = AsyncMock()
    monkeypatch.setattr(specialists, "run_local", runner)
    result = await specialists.review(
        "owner",
        "operations",
        "-----BEGIN RSA PRIVATE KEY-----",  # pragma: allowlist secret
    )
    assert result.status == "sensitive_input"
    runner.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,code,expected",
    [
        ({"result": "Useful advice", "is_error": False}, 0, "completed"),
        ({"result": "private error details", "is_error": True}, 0, "agent_error"),
        ({"unexpected": "content"}, 0, "invalid_output"),
        ({"result": "ignored"}, 1, "process_failed"),
    ],
)
async def test_runner_reports_sanitized_causes(monkeypatch, payload, code, expected):
    stream = asyncio.StreamReader()
    stream.feed_data(json.dumps(payload).encode())
    stream.feed_eof()
    proc = SimpleNamespace(
        stdout=stream, stderr=None, returncode=code, wait=AsyncMock(return_value=code)
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(specialists.asyncio, "create_subprocess_exec", spawn)
    result = await specialists.run_local(["/fixture/letta"], "fixture prompt")
    assert result.status == expected
    if expected != "completed":
        assert result.text == ""
    assert "--stateless" in spawn.call_args.args
    assert "--no-mods" in spawn.call_args.args


@pytest.mark.asyncio
async def test_timeout_kills_only_its_own_process_group(monkeypatch):
    async def slow(_size):
        await asyncio.sleep(1)

    proc = SimpleNamespace(
        stdout=SimpleNamespace(read=slow),
        stderr=None,
        returncode=None,
        pid=12345,
        wait=AsyncMock(),
    )
    monkeypatch.setattr(
        specialists.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    killed = []
    monkeypatch.setattr(specialists.os, "killpg", lambda pid, sig: killed.append(pid))
    result = await specialists.run_local(["/fixture/letta"], "fixture", timeout=0.01)
    assert result.status == "timeout" and killed == [12345]


def test_specialists_are_discoverable_and_routable(enabled):
    from agent.tooling.registry import _build_runtime_registry

    registry = _build_runtime_registry()
    for text, kind in [
        ("Compare agent tools for my coding workflow", "workflow"),
        ("Review this architecture", "engineering"),
        ("Diagnose service latency", "operations"),
    ]:
        capability = registry.select(text)
        assert capability.name == f"letta_{kind}_skill"
        assert capability.risk == "read_only"


@pytest.mark.asyncio
async def test_scout_failure_has_reason_without_exposing_output(monkeypatch):
    from services.ai_scout_reviewer import review

    monkeypatch.setenv("CURIE_AI_SCOUT_LETTA_LAUNCHER", __file__)
    monkeypatch.setattr(
        specialists,
        "run_local",
        AsyncMock(return_value=specialists.ReviewResult("timeout")),
    )
    ids, status = await review([], {})
    assert ids == [] and status.endswith(":timeout")


@pytest.mark.asyncio
async def test_concurrent_specialists_do_not_queue_duplicate_work(enabled, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(*args, **kw):
        entered.set()
        await release.wait()
        return specialists.ReviewResult("completed", "Advice")

    monkeypatch.setattr(specialists, "run_local", slow)
    first = asyncio.create_task(
        specialists.review("owner", "workflow", "Compare agent tools")
    )
    await entered.wait()
    second = await specialists.review("owner", "operations", "Diagnose service failure")
    assert second.status == "busy"
    release.set()
    assert (await first).status == "completed"


@pytest.mark.asyncio
async def test_normal_conversation_adapter_executes_specialist(enabled, monkeypatch):
    from agent.orchestration.specialist_router import SpecialistRouter
    from agent.tooling.registry import _build_runtime_registry

    runner = AsyncMock(
        return_value=specialists.ReviewResult(
            "completed",
            "Need the component boundaries before judging the architecture.",
        )
    )
    monkeypatch.setattr(specialists, "run_local", runner)
    result = await SpecialistRouter(_build_runtime_registry()).handle(
        "Review this architecture", "owner", "telegram"
    )
    assert result is not None
    assert "component boundaries" in result.text
    assert runner.await_count == 1
    argv = runner.call_args.args[0]
    assert "--system" in argv and "curie-specialist" in argv
