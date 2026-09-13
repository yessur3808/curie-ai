"""Single schema-validated routing decision for every conversational request."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import re
from typing import Any, Mapping, Sequence
import uuid

from agent.intent_router import ToolRequest, classify_request, resolve_request
from agent.tooling import get_runtime_registry

_INTENTS = {
    "conversation",
    "social",
    "system_command",
    "capability",
    "clarification",
    "multiple_intents",
    "approval",
}
_RISKS = {"none", "read_only", "mutating"}
_MULTI_SPLIT = re.compile(r"\s*(?:;|\band then\b|\bthen also\b|\balso\b)\s*", re.I)
_CONVERSATIONAL_CONTEXT = re.compile(
    r"\b(?:weather|storm|rain)\s+(?:metaphor|symbol|imagery)\b|"
    r"\b(?:my|that)\s+(?:trip|project|memory)\b.{0,80}\b(?:was|felt|seemed)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    id: str
    intent: str
    confidence: float
    parameters: Mapping[str, Any] = field(default_factory=dict)
    live_data_required: bool = False
    selected_capability: str | None = None
    risk: str = "none"
    source: str = "deterministic"
    explanation: str = ""
    alternatives: tuple[str, ...] = ()
    approval_required: bool = False

    def __post_init__(self) -> None:
        if self.intent not in _INTENTS:
            raise ValueError(f"Invalid routing intent: {self.intent}")
        if self.risk not in _RISKS:
            raise ValueError(f"Invalid routing risk: {self.risk}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Routing confidence must be between zero and one")
        if self.intent == "capability" and not self.selected_capability:
            raise ValueError("Capability decisions require selected_capability")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("Routing parameters must be an object")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decision(
    intent: str,
    *,
    confidence: float = 1.0,
    parameters: Mapping[str, Any] | None = None,
    capability: str | None = None,
    source: str = "deterministic",
    explanation: str = "",
    alternatives: tuple[str, ...] = (),
    approval_required: bool = False,
) -> RoutingDecision:
    risk, live = "none", False
    if capability:
        definition = get_runtime_registry().get(capability)
        risk = definition.risk
        live = definition.resource_policy.network
    return RoutingDecision(
        id=uuid.uuid4().hex,
        intent=intent,
        confidence=confidence,
        parameters=dict(parameters or {}),
        live_data_required=live,
        selected_capability=capability,
        risk=risk,
        source=source,
        explanation=explanation,
        alternatives=alternatives,
        approval_required=approval_required,
    )


def _from_tool_request(request: ToolRequest) -> RoutingDecision:
    if request.action == "clarify":
        return _decision(
            "clarification",
            confidence=request.confidence,
            parameters=request.params,
            source=request.source,
            explanation="Required tool details or intent ordering are ambiguous.",
        )
    if request.action in {"approve", "reject"}:
        return _decision(
            "approval",
            confidence=1.0,
            parameters={**request.params, "action": request.action},
            explanation=f"Scoped action {request.action} command.",
        )
    return _decision(
        "capability",
        confidence=request.confidence,
        parameters=request.params,
        capability=request.action,
        source=request.source,
        explanation=f"Matched the registered {request.action} capability.",
        approval_required=request.needs_approval,
    )


def _specialist(text: str) -> str | None:
    selected = get_runtime_registry().select(text)
    return selected.name if selected else None


def _deterministic_clause(
    text: str, history: Sequence[Any] | None = None
) -> RoutingDecision | None:
    request = classify_request(text, history=history)
    if request:
        return _from_tool_request(request)
    from agent.skills.system_commands import detect_system_command
    from agent.orchestration.social_service import SocialConversationService

    command = detect_system_command(text)
    if command:
        risk = (
            "mutating"
            if command in {"start", "stop", "restart", "service"}
            else "read_only"
        )
        return RoutingDecision(
            id=uuid.uuid4().hex,
            intent="system_command",
            confidence=1.0,
            parameters={"command": command},
            risk=risk,
            explanation=f"Matched explicit system command {command}.",
        )
    if SocialConversationService().handle(text) is not None:
        return _decision("social", explanation="Matched a bounded social greeting.")
    capability = _specialist(text)
    if capability:
        return _decision(
            "capability",
            confidence=0.9,
            parameters={"text": text},
            capability=capability,
            explanation=f"Matched explicit registered route {capability}.",
        )
    return None


async def route_request(
    text: str, owner_id: str = "", history: Sequence[Any] | None = None
) -> RoutingDecision:
    """Return exactly one explainable decision; model inference is read-only only."""
    clauses = [part for part in _MULTI_SPLIT.split(text.strip()) if part]
    if len(clauses) > 1:
        clause_decisions = [_deterministic_clause(part, history) for part in clauses]
        routed = [
            item for item in clause_decisions if item and item.intent != "conversation"
        ]
        capabilities = tuple(
            item.selected_capability or str(item.parameters.get("command", item.intent))
            for item in routed
        )
        if len(set(capabilities)) > 1:
            decision = _decision(
                "multiple_intents",
                parameters={
                    "message": "I found multiple independent requests. Which should I handle first: "
                    + " or ".join(item.replace("_", " ") for item in capabilities)
                    + "?"
                },
                explanation="Multiple independent intents require ordering.",
                alternatives=capabilities,
            )
            _record(owner_id, text, decision)
            return decision
    deterministic = _deterministic_clause(text, history)
    if deterministic:
        _record(owner_id, text, deterministic)
        return deterministic
    if _CONVERSATIONAL_CONTEXT.search(text):
        decision = _decision(
            "conversation",
            confidence=1.0,
            explanation="Matched a conversational use of tool-related vocabulary.",
        )
        _record(owner_id, text, decision)
        return decision
    inferred = await resolve_request(text, history=history)
    if inferred:
        decision = _from_tool_request(inferred)
        _record(owner_id, text, decision)
        return decision
    decision = _decision(
        "conversation",
        confidence=1.0,
        explanation="No high-confidence registered route matched; use normal conversation.",
    )
    _record(owner_id, text, decision)
    return decision


def _record(owner_id: str, text: str, decision: RoutingDecision) -> None:
    if not owner_id:
        return
    try:
        from memory.local_store import append_routing_outcome

        audit = decision.as_dict()
        audit["parameters"] = {
            str(key): type(value).__name__ for key, value in decision.parameters.items()
        }
        append_routing_outcome(
            owner_id,
            hashlib.sha256(text.casefold().strip().encode()).hexdigest(),
            audit,
        )
    except Exception:
        pass


def record_routing_correction(owner_id: str, decision_id: str, capability: str) -> None:
    """Store a correction without retaining message content."""
    if capability not in get_runtime_registry().names():
        raise ValueError("Routing correction must name a registered capability")
    from memory.local_store import append_routing_correction

    append_routing_correction(owner_id, decision_id, capability)
