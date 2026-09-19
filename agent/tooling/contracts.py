"""Stable contracts shared by tool routing, execution, and auditing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol


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

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        """Execute validated parameters and return a user-facing result."""


AvailabilityProbe = Callable[[], bool | tuple[bool, str | None]]


@dataclass(frozen=True, slots=True)
class ResourcePolicy:
    timeout_seconds: float = 30.0
    concurrency_limit: int = 4
    network: bool = False
    max_output_bytes: int = 64_000


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    supported: bool = False
    query_capability: str | None = None
    expected_state_projection: str = ""
    maximum_consistency_delay_seconds: float = 0.0
    contradiction_behavior: str = "report_contradiction"
    fallback: str = "report_unverified"

    def __post_init__(self) -> None:
        if self.maximum_consistency_delay_seconds < 0:
            raise ValueError("Verification consistency delay cannot be negative")
        if self.supported and not self.expected_state_projection:
            raise ValueError("Supported verification requires a state projection")


@dataclass(frozen=True, slots=True)
class IdempotencyPolicy:
    mode: str = "read_only"
    safe_retry: bool = True
    provider_key_parameter: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {
            "read_only",
            "state_reconciled",
            "provider_key",
            "receipt_only",
            "none",
        }:
            raise ValueError("Invalid capability idempotency mode")
        if self.mode == "none" and self.safe_retry:
            raise ValueError(
                "Non-idempotent capabilities cannot be marked safe to retry"
            )


@dataclass(frozen=True, slots=True)
class CompensationPolicy:
    capability: str | None = None
    captures_pre_state: bool = False
    approval_required: bool = True


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
    verification: VerificationPolicy = VerificationPolicy()
    idempotency: IdempotencyPolicy = IdempotencyPolicy()
    compensation: CompensationPolicy = CompensationPolicy()

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
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "examples": list(self.examples),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "risk": self.risk,
            "required_permissions": sorted(self.required_permissions),
            "approval_policy": self.approval_policy,
            "available": available,
            "error": reason,
            "category": self.category,
            "tags": list(self.tags),
            "chat_routable": self.chat_routable,
            "verification": {
                "supported": self.verification.supported,
                "query_capability": self.verification.query_capability,
                "expected_state_projection": self.verification.expected_state_projection,
                "maximum_consistency_delay_seconds": self.verification.maximum_consistency_delay_seconds,
                "contradiction_behavior": self.verification.contradiction_behavior,
                "fallback": self.verification.fallback,
            },
            "idempotency": {
                "mode": self.idempotency.mode,
                "safe_retry": self.idempotency.safe_retry,
                "provider_key_parameter": self.idempotency.provider_key_parameter,
            },
            "compensation": {
                "capability": self.compensation.capability,
                "captures_pre_state": self.compensation.captures_pre_state,
                "approval_required": self.compensation.approval_required,
            },
        }
