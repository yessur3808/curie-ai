"""Stable contracts shared by tool routing, execution, and auditing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ToolContext:
    internal_id: str
    profile: Mapping[str, Any] = field(default_factory=dict)
    platform: str = "unknown"
    permissions: frozenset[str] | None = None
    approved: bool = False


@dataclass(frozen=True, slots=True)
class ToolResult:
    text: str
    data: Mapping[str, Any] = field(default_factory=dict)
    source: str | None = None


class Tool(Protocol):
    name: str
    read_only: bool

    async def execute(self, params: Mapping[str, Any], context: ToolContext) -> ToolResult:
        """Execute validated parameters and return a user-facing result."""


AvailabilityProbe = Callable[[], bool | tuple[bool, str | None]]


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    timeout_seconds: float = 30.0
    concurrency_limit: int = 4
    network: bool = False
    max_output_bytes: int = 64_000


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """Authoritative executable capability metadata and policy."""

    name: str
    version: str
    display_name: str
    description: str
    examples: tuple[str, ...]
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    executor: Tool
    risk: str = "read_only"
    required_permissions: frozenset[str] = frozenset()
    approval_policy: str = "never"
    availability_probe: AvailabilityProbe | None = None
    dependency_explanation: str = ""
    routing_hints: tuple[str, ...] = ()
    confidence_threshold: float = 0.75
    resource_policy: ResourcePolicy = ResourcePolicy()
    audit_redactions: frozenset[str] = frozenset()
    category: str = "skill"
    tags: tuple[str, ...] = ()
    chat_routable: bool = True

    @property
    def read_only(self) -> bool:
        return self.risk == "read_only"

    @property
    def available(self) -> bool:
        return self.availability()[0]

    @property
    def error(self) -> str | None:
        available, reason = self.availability()
        return None if available else reason

    @property
    def module_path(self) -> str:
        return self.executor.__class__.__module__

    @property
    def entry_point(self) -> str:
        return "execute"

    def availability(self) -> tuple[bool, str | None]:
        if self.availability_probe is None:
            return True, None
        try:
            result = self.availability_probe()
            return result if isinstance(result, tuple) else (bool(result), None)
        except Exception as exc:
            return False, f"Availability probe failed: {exc}"

    def as_dict(self) -> dict[str, Any]:
        available, reason = self.availability()
        return {
            "name": self.name, "version": self.version,
            "display_name": self.display_name, "description": self.description,
            "examples": list(self.examples), "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema), "risk": self.risk,
            "required_permissions": sorted(self.required_permissions),
            "approval_policy": self.approval_policy, "available": available,
            "error": reason, "category": self.category, "tags": list(self.tags),
            "chat_routable": self.chat_routable,
        }
