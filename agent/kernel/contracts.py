"""Stable, serializable contracts for Curie's turn-understanding pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Mapping
import uuid


class ResponseMode(str, Enum):
    COMMAND_ACK = "command_ack"
    STATUS = "status"
    CLARIFICATION = "clarification"
    BRIEF = "brief"
    FOCUSED = "focused"
    DEEP = "deep"
    SOCIAL = "social"


def _safe_parameter_shape(parameters: Mapping[str, Any]) -> dict[str, str]:
    """Expose structure for diagnostics without leaking message or tool content."""
    return {str(key): type(value).__name__ for key, value in parameters.items()}


@dataclass(frozen=True, slots=True)
class EntityReference:
    surface: str
    resolved_name: str
    kind: str = "device"
    source: str = "explicit"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.resolved_name.strip():
            raise ValueError("Entity references require a resolved name")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Entity confidence must be between zero and one")

    def as_dict(self, *, include_names: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "source": self.source,
            "confidence": self.confidence,
        }
        if include_names:
            payload.update(
                {"surface": self.surface, "resolved_name": self.resolved_name}
            )
        return payload


@dataclass(frozen=True, slots=True)
class SubGoal:
    id: str
    intent: str
    capability: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        intent: str,
        *,
        capability: str | None = None,
        parameters: Mapping[str, Any] | None = None,
        depends_on: tuple[str, ...] = (),
    ) -> "SubGoal":
        return cls(
            uuid.uuid4().hex[:12],
            intent,
            capability,
            dict(parameters or {}),
            depends_on,
        )

    def as_dict(self, *, include_parameters: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent": self.intent,
            "capability": self.capability,
            "parameters": (
                dict(self.parameters)
                if include_parameters
                else _safe_parameter_shape(self.parameters)
            ),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class GoalSpec:
    id: str
    intent: str
    summary: str
    subgoals: tuple[SubGoal, ...] = ()
    constraints: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    risk: str = "none"

    def __post_init__(self) -> None:
        if self.risk not in {"none", "read_only", "mutating"}:
            raise ValueError(f"Invalid goal risk: {self.risk}")

    def as_dict(self, *, include_parameters: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "intent": self.intent,
            "summary": self.summary,
            "subgoals": [
                item.as_dict(include_parameters=include_parameters)
                for item in self.subgoals
            ],
            "constraints": list(self.constraints),
            "completion_criteria": list(self.completion_criteria),
            "risk": self.risk,
        }


@dataclass(frozen=True, slots=True)
class TurnState:
    id: str
    trace_id: str
    owner_scope_hash: str
    platform: str
    message_hash: str
    original_length: int
    goal: GoalSpec
    entities: tuple[EntityReference, ...]
    response_mode: ResponseMode
    memory_policy: str
    status: str = "understood"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def create(
        cls,
        *,
        trace_id: str,
        owner_id: str,
        platform: str,
        original_text: str,
        goal: GoalSpec,
        entities: tuple[EntityReference, ...] = (),
        response_mode: ResponseMode = ResponseMode.BRIEF,
        memory_policy: str = "relevant_only",
    ) -> "TurnState":
        return cls(
            id=uuid.uuid4().hex,
            trace_id=trace_id,
            owner_scope_hash=hashlib.sha256(str(owner_id).encode()).hexdigest()[:16],
            platform=str(platform or "unknown"),
            message_hash=hashlib.sha256(
                original_text.casefold().strip().encode()
            ).hexdigest(),
            original_length=len(original_text),
            goal=goal,
            entities=entities,
            response_mode=response_mode,
            memory_policy=memory_policy,
        )

    def as_dict(
        self, *, include_entity_names: bool = True, include_parameters: bool = False
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": self.id,
            "trace_id": self.trace_id,
            "owner_scope_hash": self.owner_scope_hash,
            "platform": self.platform,
            "message_hash": self.message_hash,
            "original_length": self.original_length,
            "goal": self.goal.as_dict(include_parameters=include_parameters),
            "entities": [
                item.as_dict(include_names=include_entity_names)
                for item in self.entities
            ],
            "response_mode": self.response_mode.value,
            "memory_policy": self.memory_policy,
            "status": self.status,
            "created_at": self.created_at,
        }
