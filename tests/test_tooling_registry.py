import asyncio

import pytest

from agent.tooling import (
    CapabilityDefinition,
    ToolContext,
    ToolRegistry,
    ToolResult,
    get_runtime_registry,
)
from agent.tooling.registry import definition

pytestmark = pytest.mark.security


class EchoTool:
    name = "echo"
    read_only = True

    async def execute(self, params, context):
        return ToolResult(text=str(params["text"]), data={"user": context.internal_id})


class BrokenTool:
    name = "broken"
    read_only = True

    async def execute(self, params, context):
        return "wrong type"


def test_registry_executes_typed_tool():
    registry = ToolRegistry([EchoTool()])
    result = asyncio.run(registry.execute("echo", {"text": "hello"}, ToolContext("u")))
    assert result == ToolResult(text="hello", data={"user": "u"})


def test_registry_rejects_duplicate_names():
    registry = ToolRegistry([EchoTool()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_registry_rejects_invalid_result_type():
    registry = ToolRegistry([BrokenTool()])
    with pytest.raises(TypeError, match="expected ToolResult"):
        asyncio.run(registry.execute("broken", {}, ToolContext("u")))


def test_runtime_registry_has_migrated_tools():
    assert {
        "weather",
        "ram_usage",
        "hardware",
        "network_speed",
        "gmail_search",
        "gmail_read",
        "gmail_send",
        "x_search",
        "x_read",
        "x_post",
        "x_reply",
        "x_dm_read",
        "x_dm_send",
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_close",
        "research",
        "inspect_project",
        "create_directory",
        "create_python_project",
        "run_tests",
        "project_change",
        "conversion",
    } <= set(get_runtime_registry().names())


def test_every_capability_has_schema_policy_and_executor():
    for capability in get_runtime_registry().all():
        assert capability.version
        assert capability.input_schema
        assert capability.output_schema
        assert capability.risk in {"read_only", "mutating"}
        assert capability.approval_policy
        assert callable(capability.executor.execute)


def test_specialists_are_selected_from_runtime_registry():
    registry = get_runtime_registry()
    assert registry.select("remind me tomorrow").name == "scheduler_skill"
    assert registry.select("show traffic directions home").name == "navigation_skill"
    assert registry.select("convert 5 km to miles").name == "conversion"


def test_conversion_executes_through_typed_registry():
    result = asyncio.run(
        get_runtime_registry().execute(
            "conversion", {"text": "convert 5 km to miles"}, ToolContext("u")
        )
    )
    assert "mile" in result.text.lower()


def test_network_speed_returns_a_completion_receipt(monkeypatch):
    import agent.tooling.system_tools as system_tools

    class FakeResponse:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"x" * 1_000_000

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

        def stream(self, *args, **kwargs):
            return FakeResponse()

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    ticks = iter((0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 1.0, 2.0, 3.0, 4.0))
    monkeypatch.setattr(system_tools.time, "perf_counter", lambda: next(ticks))

    result = asyncio.run(
        get_runtime_registry().execute("network_speed", {}, ToolContext("u"))
    )
    assert result.text.startswith("Network test complete.")
    assert set(result.data) >= {"download_mbps", "upload_mbps", "latency_ms"}


def test_duplicate_route_hints_are_rejected():
    first = definition(EchoTool(), routing_hints=(r"hello",))
    second = CapabilityDefinition(
        name="second",
        version="1",
        display_name="Second",
        description="Second echo",
        examples=(),
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        executor=EchoTool(),
        routing_hints=(r"hello",),
    )
    registry = ToolRegistry([first])
    with pytest.raises(ValueError, match="Ambiguous route"):
        registry.register(second)


def test_diagnostics_explain_every_advertised_capability():
    rows = get_runtime_registry().diagnostics()
    assert rows
    assert {row["status"] for row in rows} <= {
        "registered",
        "reachable",
        "permission_blocked",
        "dependency_missing",
    }
    assert all(row["status"] == "reachable" or row["reason"] for row in rows)


def test_schema_validation_rejects_missing_required_input():
    with pytest.raises(ValueError, match="Missing required parameters"):
        asyncio.run(get_runtime_registry().execute("research", {}, ToolContext("u")))


def test_router_weather_uses_registry(monkeypatch, tmp_path):
    import agent.action_router as router
    import memory.local_store as local_store

    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")

    class FakeRegistry:
        async def execute(self, name, params, context):
            assert name == "weather"
            assert context.internal_id == "u"
            return ToolResult("Registry weather result", source="test")

    monkeypatch.setattr("agent.tooling.get_runtime_registry", lambda: FakeRegistry())
    request = router.classify_request("Is it raining in Hong Kong?")
    result = asyncio.run(router.execute_request(request, "u", {}))
    assert result == "Registry weather result"


def test_project_change_uses_agent_model_role(monkeypatch, tmp_path):
    from agent.tooling.project_tools import _apply_project_change

    def fake_ask(prompt, **kwargs):
        assert "careful coding agent" in prompt
        assert kwargs["role"] == "agent"
        return '{"files":{"hello.py":"print(\'hello\')\\n"},"summary":"Created it."}'

    monkeypatch.setattr("llm.manager.ask_llm", fake_ask)
    text, changed = _apply_project_change("Create hello.py", tmp_path)

    assert changed == ["hello.py"]
    assert (tmp_path / "hello.py").read_text() == "print('hello')\n"
    assert "Created it" in text
