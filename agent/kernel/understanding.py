"""Deterministic first-pass understanding that produces a typed turn state."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Sequence
import uuid

from agent.response_planner import select_response_mode
from agent.routing import RoutingDecision, route_operational_request
from .contracts import EntityReference, GoalSpec, SubGoal, TurnState

_CORRELATE_AND_CONTROL = re.compile(
    r"^(?:please\s+)?(?:correlate|associate|map)\s+(?P<alias>.+?)\s+"
    r"(?:with|to)\s+(?P<device>.+?)\s+(?:and\s+)(?:then\s+)?"
    r"(?:turn|switch|power)\s+(?:it|that|the device)\s+(?P<state>on|off)"
    r"(?:\s+please)?[.!?]?$",
    re.I | re.S,
)


@dataclass(frozen=True, slots=True)
class TurnAnalysis:
    state: TurnState
    effective_text: str
    operational_decisions: tuple[RoutingDecision, ...] = ()

    @property
    def operational_decision(self) -> RoutingDecision | None:
        return (
            self.operational_decisions[0]
            if len(self.operational_decisions) == 1
            else None
        )


def _targets(decision: RoutingDecision) -> list[str]:
    values = [str(item).strip() for item in decision.parameters.get("targets", ())]
    target = str(decision.parameters.get("target") or "").strip()
    if target:
        values.insert(0, target)
    if decision.selected_capability == "home_alias":
        alias = str(decision.parameters.get("alias") or "").strip()
        device = str(decision.parameters.get("device") or "").strip()
        values = [item for item in (alias, device) if item]
    return list(dict.fromkeys(item for item in values if item))


def _compound_home_plan(
    text: str, owner_id: str, history: Sequence[Any] | None
) -> tuple[RoutingDecision, ...]:
    match = _CORRELATE_AND_CONTROL.fullmatch(text.strip())
    if not match:
        return ()
    alias = match.group("alias").strip(" \t.,!?")
    device = match.group("device").strip(" \t.,!?")
    state = match.group("state").casefold()
    alias_decision = route_operational_request(
        f"Remember that {device} is {alias}", owner_id, history
    )
    control_decision = route_operational_request(
        f"Turn {device} {state}", owner_id, history
    )
    if not alias_decision or not control_decision:
        return ()
    if (
        alias_decision.selected_capability != "home_alias"
        or control_decision.selected_capability != "home_control"
    ):
        return ()
    return alias_decision, control_decision


def analyze_turn(
    original_text: str,
    effective_text: str,
    *,
    owner_id: str,
    platform: str,
    history: Sequence[Any] | None = None,
    trace_id: str | None = None,
    resolved_entities: tuple[EntityReference, ...] = (),
) -> TurnAnalysis:
    """Create a bounded goal model before any memory retrieval or model prompt."""
    trace_id = trace_id or uuid.uuid4().hex
    decisions = _compound_home_plan(effective_text, owner_id, history)
    if not decisions:
        decision = route_operational_request(effective_text, owner_id, history)
        decisions = (decision,) if decision else ()

    subgoals: list[SubGoal] = []
    entities = list(resolved_entities)
    previous_id: str | None = None
    for decision in decisions:
        subgoal = SubGoal.create(
            decision.intent,
            capability=decision.selected_capability,
            parameters=decision.parameters,
            depends_on=(previous_id,) if previous_id else (),
        )
        previous_id = subgoal.id
        subgoals.append(subgoal)
        for target in _targets(decision):
            if all(
                item.resolved_name.casefold() != target.casefold() for item in entities
            ):
                entities.append(EntityReference(target, target, source="explicit"))

    if decisions:
        intent = "compound_operation" if len(decisions) > 1 else decisions[0].intent
        risk = (
            "mutating"
            if any(item.risk == "mutating" for item in decisions)
            else decisions[0].risk
        )
        capability = decisions[-1].selected_capability
        summary = (
            "Execute an ordered operational plan"
            if len(decisions) > 1
            else f"Execute {capability or intent}"
        )
    else:
        intent, risk, capability = "conversation", "none", None
        summary = "Answer the current conversational request"
        subgoals.append(SubGoal.create(intent))

    mode = select_response_mode(
        effective_text,
        routing_intent=(decisions[-1].intent if decisions else intent),
        capability=capability,
    )
    goal = GoalSpec(
        id=uuid.uuid4().hex,
        intent=intent,
        summary=summary,
        subgoals=tuple(subgoals),
        constraints=("Do not claim an unverified external result",),
        completion_criteria=(
            ("Report the verified result or a specific blocker",)
            if decisions
            else ("Answer the newest request without reviving stale topics",)
        ),
        risk=risk,
    )
    state = TurnState.create(
        trace_id=trace_id,
        owner_id=owner_id,
        platform=platform,
        original_text=original_text,
        goal=goal,
        entities=tuple(entities),
        response_mode=mode,
        memory_policy="operational_minimal" if decisions else "relevant_only",
    )
    return TurnAnalysis(state, effective_text, tuple(decisions))
