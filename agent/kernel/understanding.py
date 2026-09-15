"""Deterministic first-pass understanding that produces a typed turn state."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Sequence
import uuid

from agent.response_planner import select_response_mode
from agent.routing import RoutingDecision, route_operational_request
from .contracts import EntityReference, GoalConstraint, GoalSpec, SubGoal, TurnState

_CORRELATE_AND_CONTROL = re.compile(
    r"^(?:please\s+)?(?:correlate|associate|map)\s+(?P<alias>.+?)\s+"
    r"(?:with|to)\s+(?P<device>.+?)\s+(?:and\s+)(?:then\s+)?"
    r"(?:turn|switch|power)\s+(?:it|that|the device)\s+(?P<state>on|off)"
    r"(?:\s+please)?[.!?]?$",
    re.I | re.S,
)
_SEQUENTIAL_SPLIT = re.compile(
    r"\s*(?:;|\band then\b|\bthen also\b|\bafter that\b)\s*", re.I
)
_PARALLEL_SPLIT = re.compile(r"\s+\band\b\s+", re.I)


@dataclass(frozen=True, slots=True)
class TurnAnalysis:
    state: TurnState
    effective_text: str
    operational_decisions: tuple[RoutingDecision, ...] = ()
    preserve_order: bool = True

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


def _typed_constraints(
    decisions: tuple[RoutingDecision, ...], text: str
) -> tuple[GoalConstraint, ...]:
    constraints: list[GoalConstraint] = []
    tracked = {
        "target": "device_target",
        "targets": "device_targets",
        "state": "requested_state",
        "provider": "provider",
        "city": "location",
        "location": "location",
        "unit": "unit",
        "limit": "result_limit",
        "path": "workspace_path",
    }
    for decision in decisions:
        for key, kind in tracked.items():
            value = decision.parameters.get(key)
            if value is not None and value != "" and value != () and value != []:
                constraints.append(GoalConstraint(kind, value))
    if re.search(r"\b(?:brief|briefly|short answer|keep it short)\b", text, re.I):
        constraints.append(GoalConstraint("response_length", "brief"))
    if re.search(r"\b(?:table|tabular)\b", text, re.I):
        constraints.append(GoalConstraint("response_format", "table"))
    elif re.search(r"\b(?:bullet points?|bulleted list)\b", text, re.I):
        constraints.append(GoalConstraint("response_format", "bullets"))
    return tuple(constraints)


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


def _compound_operational_plan(
    text: str, owner_id: str, history: Sequence[Any] | None
) -> tuple[tuple[RoutingDecision, ...], bool]:
    home = _compound_home_plan(text, owner_id, history)
    if home:
        return home, True

    sequential = [item for item in _SEQUENTIAL_SPLIT.split(text.strip()) if item]
    if len(sequential) > 1:
        decisions = tuple(
            decision
            for item in sequential
            if (decision := route_operational_request(item, owner_id, history))
        )
        if len(decisions) == len(sequential):
            return decisions, True

    # A plain "and" is decomposed only when every resulting clause is
    # independently and deterministically routable. Read-only work can run in
    # parallel. Mutations stay ordered. This protects device lists, prose, and
    # model-inferred intents from accidental decomposition.
    parallel = [item for item in _PARALLEL_SPLIT.split(text.strip()) if item]
    if 1 < len(parallel) <= 4:
        decisions = tuple(
            decision
            for item in parallel
            if (decision := route_operational_request(item, owner_id, history))
        )
        if len(decisions) == len(parallel):
            read_only = all(item.risk in {"none", "read_only"} for item in decisions)
            return decisions, not read_only
    return (), True


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
    decisions, preserve_order = _compound_operational_plan(
        effective_text, owner_id, history
    )
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
            depends_on=(previous_id,) if preserve_order and previous_id else (),
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
        typed_constraints=_typed_constraints(tuple(decisions), effective_text),
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
    return TurnAnalysis(state, effective_text, tuple(decisions), preserve_order)
