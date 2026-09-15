"""One authoritative registry for discovery, routing, policy, and execution."""
from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from agent.tooling.contracts import CapabilityDefinition, ResourcePolicy, Tool, ToolContext, ToolResult

_OBJECT = {"type": "object", "properties": {}}
_TEXT_INPUT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
_TEXT_OUTPUT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def _schema_for(name: str) -> Mapping[str, Any]:
    return {
        "weather": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "research": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        "inspect_project": {"type": "object", "properties": {"path": {"type": "string"}}},
        "create_directory": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "create_python_project": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "run_tests": {"type": "object", "properties": {"path": {"type": "string"}}},
        "project_change": {"type": "object", "properties": {"request": {"type": "string"}, "path": {"type": "string"}, "run_tests": {"type": "boolean"}}, "required": ["request"]},
        "gmail_search": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        "gmail_read": {"type": "object", "properties": {"message_id": {"type": "string"}}, "required": ["message_id"]},
        "gmail_send": {"type": "object", "properties": {"recipient": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["recipient", "subject", "body"]},
        "x_search": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        "x_read": {"type": "object", "properties": {"post_id": {"type": "string"}}, "required": ["post_id"]},
        "x_post": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "x_reply": {"type": "object", "properties": {"post_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["post_id", "text"]},
        "x_dm_read": {"type": "object", "properties": {"limit": {"type": "integer"}}},
        "x_dm_send": {"type": "object", "properties": {"participant_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["participant_id", "text"]},
        "browser_open": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        "browser_snapshot": _OBJECT,
        "browser_click": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "browser_fill": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "string"}}, "required": ["label", "value"]},
        "browser_close": _OBJECT,
        "home_status": {"type": "object", "properties": {"target": {"type": "string"}, "provider": {"type": "string"}}},
        "home_control": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "targets": {"type": "array"},
                "state": {"type": "string", "enum": ["on", "off"]},
                "provider": {"type": "string"},
            },
            "required": ["state"],
        },
        "home_alias": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "alias": {"type": "string"},
            },
            "required": ["device", "alias"],
        },
    }.get(name, _OBJECT)


def _validate(schema: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    if schema.get("type") != "object" or not isinstance(value, Mapping):
        raise ValueError("Capability input must be an object")
    missing = [key for key in schema.get("required", ()) if key not in value]
    if missing:
        raise ValueError(f"Missing required parameters: {', '.join(missing)}")
    types = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": (list, tuple)}
    for key, item in schema.get("properties", {}).items():
        if key in value and item.get("type") in types and not isinstance(value[key], types[item["type"]]):
            raise ValueError(f"Parameter {key!r} must be {item['type']}")
        if key in value and item.get("enum") and value[key] not in item["enum"]:
            raise ValueError(f"Parameter {key!r} must be one of {', '.join(map(str, item['enum']))}")


def definition(tool: Tool, **overrides: Any) -> CapabilityDefinition:
    read_only, name = bool(getattr(tool, "read_only", True)), str(tool.name)
    return CapabilityDefinition(
        name=name, version="1.0", display_name=overrides.pop("display_name", name.replace("_", " ").title()),
        description=overrides.pop("description", f"Execute Curie's {name.replace('_', ' ')} capability."),
        examples=tuple(overrides.pop("examples", ())), input_schema=overrides.pop("input_schema", _schema_for(name)),
        output_schema=overrides.pop("output_schema", _TEXT_OUTPUT), executor=tool,
        risk="read_only" if read_only else "mutating",
        approval_policy=overrides.pop("approval_policy", "never" if read_only else "per_invocation"),
        required_permissions=frozenset(overrides.pop("required_permissions", ())),
        resource_policy=overrides.pop("resource_policy", ResourcePolicy(network=name in {"weather", "research"})),
        **overrides,
    )


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool | CapabilityDefinition] = ()):
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._routing_hints: dict[str, str] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        for tool in tools:
            self.register(tool)

    def register(self, item: Tool | CapabilityDefinition) -> None:
        capability = item if isinstance(item, CapabilityDefinition) else definition(item)
        name = capability.name.strip()
        if not name:
            raise ValueError("Capability name cannot be empty")
        if name in self._capabilities:
            raise ValueError(f"Capability {name!r} is already registered")
        if capability.risk not in {"read_only", "mutating"}:
            raise ValueError(f"Capability {name!r} has invalid risk classification")
        if not capability.input_schema or not capability.output_schema:
            raise ValueError(f"Capability {name!r} requires input and output schemas")
        for hint in capability.routing_hints:
            normalized = hint.strip().casefold()
            if normalized in self._routing_hints:
                raise ValueError(f"Ambiguous route {hint!r} belongs to {self._routing_hints[normalized]!r} and {name!r}")
            re.compile(hint)
            self._routing_hints[normalized] = name
        self._capabilities[name] = capability
        self._semaphores[name] = asyncio.Semaphore(capability.resource_policy.concurrency_limit)

    def reset(self) -> None:
        self._capabilities.clear(); self._routing_hints.clear(); self._semaphores.clear()

    def get(self, name: str) -> CapabilityDefinition:
        try: return self._capabilities[name]
        except KeyError as exc: raise KeyError(f"Unknown capability: {name}") from exc

    def names(self) -> tuple[str, ...]: return tuple(sorted(self._capabilities))
    def all(self) -> list[CapabilityDefinition]: return sorted(self._capabilities.values(), key=lambda item: (item.category, item.name))
    def available_tools(self) -> list[CapabilityDefinition]: return [item for item in self.all() if item.available]
    def by_category(self, category: str) -> list[CapabilityDefinition]: return [item for item in self.all() if item.category == category]
    def by_tag(self, tag: str) -> list[CapabilityDefinition]: return [item for item in self.all() if tag in item.tags]
    def reload(self) -> None: return None

    def summary(self) -> dict[str, Any]:
        items, categories = self.all(), {}
        for item in items: categories.setdefault(item.category, []).append(item.as_dict())
        return {"total": len(items), "available": sum(item.available for item in items), "unavailable": sum(not item.available for item in items), "categories": categories}

    def select(self, text: str) -> CapabilityDefinition | None:
        for capability in self._capabilities.values():
            if capability.chat_routable and capability.available and any(re.search(hint, text, re.I) for hint in capability.routing_hints):
                return capability
        return None

    def diagnostics(self, context: ToolContext | None = None) -> list[dict[str, Any]]:
        rows = []
        for capability in self.all():
            available, reason = capability.availability()
            if not available: status = "dependency_missing"
            elif context and context.permissions is not None and not capability.required_permissions <= context.permissions:
                status, reason = "permission_blocked", "Required permission is not granted"
            elif capability.chat_routable: status = "reachable"
            else: status, reason = "registered", reason or "Not routed from normal chat"
            rows.append({"name": capability.name, "status": status, "reason": reason})
        return rows

    async def execute(self, name: str, params: Mapping[str, Any], context: ToolContext) -> ToolResult:
        capability = self.get(name)
        available, reason = capability.availability()
        if not available: raise RuntimeError(reason or f"Capability {name!r} is unavailable")
        _validate(capability.input_schema, params)
        if context.permissions is not None and not capability.required_permissions <= context.permissions:
            raise PermissionError(f"Missing permissions for capability {name!r}")
        if capability.approval_policy == "per_invocation" and context.permissions is not None and not context.approved:
            raise PermissionError(f"Capability {name!r} requires per-invocation approval")
        async with self._semaphores[name]:
            result = await asyncio.wait_for(capability.executor.execute(params, context), timeout=capability.resource_policy.timeout_seconds)
        if not isinstance(result, ToolResult): raise TypeError(f"Capability {name!r} returned {type(result).__name__}, expected ToolResult")
        if len(result.text.encode()) > capability.resource_policy.max_output_bytes: raise ValueError(f"Capability {name!r} exceeded its output limit")
        return result


def _module_probe(module: str):
    def probe() -> tuple[bool, str | None]:
        try: importlib.import_module(module); return True, None
        except ImportError as exc: return False, f"Dependency missing: {exc}"
    return probe


class _ComponentTool:
    read_only = True
    def __init__(self, name: str): self.name = name
    async def execute(self, params, context) -> ToolResult:
        return ToolResult(f"{self.name} is registered as an operational component.")


def _dependency_probe(module: str, env: tuple[str, ...] = ()):
    def probe() -> tuple[bool, str | None]:
        import os
        missing = [name for name in env if not os.getenv(name)]
        if missing: return False, f"Missing configuration: {', '.join(missing)}"
        return _module_probe(module)()
    return probe


def _coding_service_probe() -> tuple[bool, str | None]:
    import os
    if os.getenv("RUN_CODING_SERVICE", "false").casefold() != "true":
        return False, "Standalone coding service is disabled"
    credentials = ("GITHUB_TOKEN", "GITLAB_TOKEN", "BITBUCKET_APP_PASSWORD")
    if not any(os.getenv(name) for name in credentials):
        return False, "No supported code-host credentials are configured"
    return _module_probe("services.coding_service")()


def _build_runtime_registry() -> ToolRegistry:
    from agent.tooling.account_tools import GmailReadTool, GmailSearchTool, GmailSendTool, XPostTool, XReadDMsTool, XReadTool, XReplyTool, XSearchTool, XSendDMTool
    from agent.tooling.browser_tools import BrowserClickTool, BrowserCloseTool, BrowserFillTool, BrowserOpenTool, BrowserSnapshotTool
    from agent.tooling.project_tools import CreateDirectoryTool, CreatePythonProjectTool, InspectProjectTool, ProjectChangeTool, RunTestsTool
    from agent.tooling.conversion_tool import ConversionTool
    from agent.tooling.research_tool import ResearchTool
    from agent.tooling.specialist_tools import SpecialistTool, browser, coding, http_interceptor, navigation, network_analyzer, network_scanner, scheduler, trip_planner
    from agent.tooling.system_tools import HardwareTool, NetworkSpeedTool, RamUsageTool
    from agent.tooling.smart_home_tools import HomeAliasTool, HomeControlTool, HomeStatusTool
    from agent.tooling.weather_tool import WeatherTool
    capabilities = [
        definition(WeatherTool(), description="Get weather from a live source.", tags=("weather", "live")),
        definition(RamUsageTool(), description="Inspect current memory usage.", chat_routable=False, tags=("system",)),
        definition(HardwareTool(), description="Inspect local hardware.", chat_routable=False, tags=("system", "hardware")),
        definition(
            NetworkSpeedTool(),
            description="Measure current download speed, upload speed, and latency from Curie's host.",
            resource_policy=ResourcePolicy(timeout_seconds=50, concurrency_limit=1, network=True),
            tags=("system", "network", "live"),
        ),
        definition(GmailSearchTool(), resource_policy=ResourcePolicy(timeout_seconds=45, concurrency_limit=2, network=True), tags=("gmail", "email", "read")),
        definition(GmailReadTool(), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=2, network=True), tags=("gmail", "email", "read")),
        definition(GmailSendTool(), required_permissions=("gmail_send",), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=1, network=True), audit_redactions=frozenset({"body"}), tags=("gmail", "email", "write")),
        definition(XSearchTool(), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=2, network=True), tags=("x", "social", "read")),
        definition(XReadTool(), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=2, network=True), tags=("x", "social", "read")),
        definition(XPostTool(), required_permissions=("x_write",), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=1, network=True), audit_redactions=frozenset({"text"}), tags=("x", "social", "write")),
        definition(XReplyTool(), required_permissions=("x_write",), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=1, network=True), audit_redactions=frozenset({"text"}), tags=("x", "social", "write")),
        definition(XReadDMsTool(), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=1, network=True), audit_redactions=frozenset({"text"}), tags=("x", "dm", "read")),
        definition(XSendDMTool(), required_permissions=("x_dm_write",), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=1, network=True), audit_redactions=frozenset({"text"}), tags=("x", "dm", "write")),
        definition(BrowserOpenTool(), availability_probe=_module_probe("playwright.async_api"), resource_policy=ResourcePolicy(timeout_seconds=45, concurrency_limit=2, network=True), tags=("browser", "interactive", "read")),
        definition(BrowserSnapshotTool(), availability_probe=_module_probe("playwright.async_api"), resource_policy=ResourcePolicy(timeout_seconds=20, concurrency_limit=2), tags=("browser", "interactive", "read")),
        definition(BrowserClickTool(), availability_probe=_module_probe("playwright.async_api"), required_permissions=("browser_interact",), resource_policy=ResourcePolicy(timeout_seconds=45, concurrency_limit=1, network=True), tags=("browser", "interactive", "write")),
        definition(BrowserFillTool(), availability_probe=_module_probe("playwright.async_api"), required_permissions=("browser_interact",), resource_policy=ResourcePolicy(timeout_seconds=30, concurrency_limit=1), audit_redactions=frozenset({"value"}), tags=("browser", "interactive", "write")),
        definition(BrowserCloseTool(), availability_probe=_module_probe("playwright.async_api"), resource_policy=ResourcePolicy(timeout_seconds=20, concurrency_limit=2), tags=("browser", "interactive")),
        definition(ResearchTool(), description="Research current information using live sources.", tags=("web", "research")),
        definition(InspectProjectTool(), description="Inspect an allowed project root.", tags=("coding", "filesystem")),
        definition(CreateDirectoryTool(), required_permissions=("write_project",), tags=("coding", "filesystem")),
        definition(CreatePythonProjectTool(), required_permissions=("write_project",), tags=("coding",)),
        definition(RunTestsTool(), description="Run tests inside the command sandbox.", tags=("coding", "testing")),
        definition(ProjectChangeTool(), required_permissions=("write_project",), tags=("coding", "filesystem")),
        definition(
            HomeStatusTool(),
            description="Read and analyze normalized status and telemetry across Curie's configured home devices.",
            examples=("What's running at home?", "Is the bedroom lamp on?"),
            resource_policy=ResourcePolicy(timeout_seconds=60, concurrency_limit=2, network=True),
            tags=("smart-home", "iot", "status"),
        ),
        definition(
            HomeControlTool(),
            description="Turn one or more unambiguously resolved smart-home devices on or off and verify their resulting state.",
            examples=("Turn off the desk plug", "Switch the floor lamp and TV light on"),
            approval_policy="never",
            required_permissions=("home_control",),
            resource_policy=ResourcePolicy(timeout_seconds=60, concurrency_limit=1, network=True),
            tags=("smart-home", "iot", "control"),
        ),
        definition(
            HomeAliasTool(),
            description="Remember an owner-scoped natural-language alias for a smart-home device.",
            examples=("Remember that AI Sync Box strip is the TV light",),
            approval_policy="never",
            required_permissions=("home_control",),
            resource_policy=ResourcePolicy(timeout_seconds=60, concurrency_limit=1, network=True),
            tags=("smart-home", "iot", "memory"),
        ),
        definition(
            ConversionTool(), display_name="Unit and Currency Conversion",
            description="Convert units deterministically or currencies from a live rate source.",
            input_schema=_TEXT_INPUT, routing_hints=(r"(?=.*\d)(?=.*\b(?:to|into|in)\b)(?:\bconvert\b|\bhow many\b|\bhow much\b|\bwhat(?:'s| is)\b|^\s*[\d.,]+)",),
            resource_policy=ResourcePolicy(timeout_seconds=15, concurrency_limit=4, network=True),
            tags=("conversion", "utilities"),
        ),
    ]
    specs = (
        ("scheduler_skill", "Scheduler", scheduler, False, r"\b(?:remind|reminder|alarm|schedule|timer)\b", "agent.skills.scheduler", ("reminders",)),
        ("navigation_skill", "Navigation", navigation, True, r"\b(?:directions?|navigate|navigation|traffic|route to|how do i get)\b", "agent.skills.navigation", ("navigation",)),
        ("trip_planner_skill", "Trip Planner", trip_planner, True, r"\b(?:vacation|holiday|trip plan|travel plan|itinerary)\b", "agent.skills.trip_planner", ("travel",)),
        (
            "browser_skill",
            "Browser",
            browser,
            True,
            r"^\s*https?://\S+\s*$|"
            r"\b(?:open|browse|visit|read|summari[sz]e|check|inspect)\b.{0,80}https?://|"
            r"\b(?:open|browse|visit)\s+(?:the\s+)?(?:webpage|website|site|url)\b",
            "agent.skills.browser",
            ("web",),
        ),
        ("network_scanner_skill", "Network Scanner", network_scanner, True, r"\b(?:port scan|network scan|nmap|scan hosts?)\b", "agent.skills.network_scanner", ("network", "security")),
        ("network_analyzer_skill", "Network Analyzer", network_analyzer, True, r"\b(?:pcap|packet capture|wireshark|network traffic|protocol analysis)\b", "agent.skills.network_analyzer", ("network", "security")),
        ("http_interceptor_skill", "HTTP Interceptor", http_interceptor, True, r"\b(?:http interceptor|intercept http|burp suite|web proxy|proxy request)\b", "agent.skills.http_interceptor", ("web", "security")),
        ("coding_skill", "Coding Assistant", coding, True, r"\b(?:code|coding|programming|python|javascript|typescript|function|class|algorithm|compile)\b", "agent.skills.coding_assistant", ("coding",)),
    )
    for name, display, handler, read_only, hint, module, tags in specs:
        capabilities.append(CapabilityDefinition(
            name=name, version="1.0", display_name=display, description=f"Handle {display.lower()} requests through active chat.", examples=(),
            input_schema=_TEXT_INPUT, output_schema=_TEXT_OUTPUT, executor=SpecialistTool(name, handler, read_only=read_only),
            risk="read_only" if read_only else "mutating", required_permissions=frozenset({"schedule_reminder"} if not read_only else ()),
            approval_policy="runtime" if not read_only else "never", availability_probe=_module_probe(module), routing_hints=(hint,),
            confidence_threshold=.8, resource_policy=ResourcePolicy(timeout_seconds=120, concurrency_limit=2, network=name in {"browser_skill", "navigation_skill", "trip_planner_skill"}), tags=tags,
        ))
    components = (
        ("api", "REST API", "connectors.api", (), "connector", ("api",)),
        ("telegram", "Telegram", "connectors.telegram", ("TELEGRAM_BOT_TOKEN",), "connector", ("messaging",)),
        ("discord", "Discord", "connectors.discord_bot", ("DISCORD_BOT_TOKEN",), "connector", ("messaging",)),
        ("slack", "Slack", "connectors.slack", ("SLACK_BOT_TOKEN",), "connector", ("messaging",)),
        ("whatsapp", "WhatsApp", "connectors.whatsapp", (), "connector", ("messaging",)),
        ("cron", "Cron Runner", "services.cron_runner", (), "service", ("scheduling",)),
        ("sessions", "Session Manager", "memory.session_manager", (), "service", ("memory",)),
        ("proactive_messaging", "Proactive Messaging", "services.proactive_messaging", (), "service", ("proactive",)),
        ("canvas", "Live Canvas", "cli.canvas_webview", (), "canvas", ("canvas", "nodes")),
        ("dashboard", "Agent Dashboard", "cli.agent_webview", (), "canvas", ("dashboard",)),
    )
    for name, display, module, env, category, tags in components:
        capabilities.append(CapabilityDefinition(
            name=name, version="1.0", display_name=display,
            description=f"Operational {display} component.", examples=(), input_schema=_OBJECT,
            output_schema=_TEXT_OUTPUT, executor=_ComponentTool(name), risk="read_only",
            availability_probe=_dependency_probe(module, env), dependency_explanation=f"Requires {module}",
            category=category, tags=tags, chat_routable=False,
        ))
    capabilities.append(CapabilityDefinition(
        name="coding_service", version="1.0", display_name="Optional Coding Service",
        description="Standalone code-host automation service.", examples=(), input_schema=_OBJECT,
        output_schema=_TEXT_OUTPUT, executor=_ComponentTool("coding_service"), risk="mutating",
        required_permissions=frozenset({"external_code_host"}), approval_policy="per_invocation",
        availability_probe=_coding_service_probe, dependency_explanation="Requires explicit enablement and code-host credentials",
        category="service", tags=("coding",), chat_routable=False,
    ))
    return ToolRegistry(capabilities)


_runtime_registry: ToolRegistry | None = None
def get_runtime_registry() -> ToolRegistry:
    global _runtime_registry
    if _runtime_registry is None: _runtime_registry = _build_runtime_registry()
    return _runtime_registry
def reset_runtime_registry() -> None:
    global _runtime_registry
    _runtime_registry = None
