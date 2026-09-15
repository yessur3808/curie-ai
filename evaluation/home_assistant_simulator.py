"""Small deterministic Home Assistant simulator for CurieEval regressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Any


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


@dataclass(slots=True)
class SimulatedEntity:
    entity_id: str
    name: str
    state: str = "off"
    aliases: tuple[str, ...] = ()
    available: bool = True
    controllable: bool = True
    transition: str = "immediate"


@dataclass(frozen=True, slots=True)
class SimulatedResult:
    text: str
    verification_status: str
    resolved_entity: str | None
    tool_sequence: tuple[str, ...]
    data: dict[str, Any] = field(default_factory=dict)

    def as_response(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "verification_status": self.verification_status,
            "resolved_entity": self.resolved_entity,
            "tool_sequence": list(self.tool_sequence),
            "data": dict(self.data),
        }


class HomeAssistantSimulator:
    """Model entity resolution, service calls, lag, failure, and verification."""

    def __init__(self, entities: list[SimulatedEntity] | None = None):
        self.entities = {
            item.entity_id: item
            for item in (
                entities
                or [
                    SimulatedEntity(
                        "light.ai_sync_box_strip",
                        "AI Sync Box strip",
                        "on",
                        ("dreamview", "dream view", "tv light", "tv backlight"),
                    )
                ]
            )
        }
        self.calls: list[dict[str, str]] = []
        self._pending: dict[str, str] = {}

    def resolve(self, target: str) -> SimulatedEntity:
        query = _normalize(target)
        ranked: list[tuple[float, SimulatedEntity]] = []
        for entity in self.entities.values():
            names = (entity.name, entity.entity_id, *entity.aliases)
            normalized = [_normalize(item) for item in names]
            if query in normalized:
                score = 1.0
            elif any(query and (query in item or item in query) for item in normalized):
                score = 0.92
            else:
                score = max(
                    (
                        SequenceMatcher(
                            None, query.replace(" ", ""), item.replace(" ", "")
                        ).ratio()
                        for item in normalized
                    ),
                    default=0.0,
                )
            if score >= 0.74:
                ranked.append((score, entity))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            raise LookupError(f"No entity matches {target!r}")
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
            names = ", ".join(item.name for _, item in ranked[:5])
            raise ValueError(f"That target is ambiguous: {names}")
        return ranked[0][1]

    def tick(self) -> None:
        for entity_id, state in tuple(self._pending.items()):
            self.entities[entity_id].state = state
            self._pending.pop(entity_id, None)

    def control(
        self, target: str, state: str, *, verify_ticks: int = 1
    ) -> SimulatedResult:
        if state not in {"on", "off"}:
            raise ValueError("State must be on or off")
        entity = self.resolve(target)
        tools = ["home_assistant.resolve_entity", "home_assistant.get_state"]
        if not entity.available:
            return SimulatedResult(
                f"I can't reach {entity.name}. It's unavailable, so I didn't send the command.",
                "failed",
                entity.entity_id,
                tuple(tools),
            )
        if not entity.controllable:
            return SimulatedResult(
                f"{entity.name} does not expose on/off control.",
                "failed",
                entity.entity_id,
                tuple(tools),
            )
        if entity.state == state:
            return SimulatedResult(
                f"The {entity.name} is already {state}, monsieur.",
                "already_satisfied",
                entity.entity_id,
                tuple(tools),
            )

        service = f"home_assistant.turn_{state}"
        tools.append(service)
        self.calls.append({"service": service, "entity_id": entity.entity_id})
        if entity.transition == "error":
            return SimulatedResult(
                f"I couldn't send the command to {entity.name}.",
                "failed",
                entity.entity_id,
                tuple(tools),
            )
        if entity.transition == "immediate":
            entity.state = state
        elif entity.transition == "lagged":
            self._pending[entity.entity_id] = state
        tools.append("home_assistant.get_state")
        for _ in range(max(0, int(verify_ticks))):
            if entity.state == state:
                break
            self.tick()
        if entity.state == state:
            return SimulatedResult(
                f"Done. {entity.name} is now {state}.",
                "verified",
                entity.entity_id,
                tuple(tools),
            )
        return SimulatedResult(
            f"The command was accepted, but I couldn't verify that {entity.name} is {state} yet.",
            "unverified",
            entity.entity_id,
            tuple(tools),
        )
