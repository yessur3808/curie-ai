"""Deterministic first-pass understanding that produces a typed turn state."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Sequence
import uuid

from agent.response_planner import select_response_mode
from agent.routing import RoutingDecision, route_operational_request
from agent.understanding.decomposition import CompoundRequest, decompose_request
from agent.understanding.recognizers import Recognition, recognize_deterministic
from .contracts import EntityReference, GoalConstraint, GoalSpec, SubGoal, TurnState

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
    preserve_order: bool = True
    recognition: Recognition | None = None
    decomposition: CompoundRequest | None = None

    @property
    def operational_decision(self) -> RoutingDecision | None:
        return (
            self.operational_decisions[0]
            if len(self.operational_decisions) == 1
            else None
        )

    @property
    def dependency_kinds(self) -> dict[int, str]:
        """Map live plan-step indexes to their explicit dependency semantics."""
        if not self.decomposition:
            return {}
        return {
            index: clause.relation.value
            for index, clause in enumerate(self.decomposition.clauses)
            if clause.depends_on
        }


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
) -> tuple[tuple[RoutingDecision, ...], bool, CompoundRequest | None]:
    home = _compound_home_plan(text, owner_id, history)
    if home:
        return home, True, None

    routed: dict[str, RoutingDecision] = {}

    def meaningful(clause: str) -> bool:
        decision = route_operational_request(clause, owner_id, history)
        if decision is not None:
            routed[clause] = decision
            return True
        return False

    decomposition = decompose_request(text, independently_meaningful=meaningful)
    if decomposition.decomposed:
        decisions = tuple(routed[item.text] for item in decomposition.clauses)
        mutations = any(item.risk == "mutating" for item in decisions)
        return decisions, decomposition.preserve_order or mutations, decomposition
    return (), True, decomposition


def analyze_turn(
    original_text: str,
    effective_text: str,
    *,
    owner_id: str,
    platform: str,
    history: Sequence[Any] | None = None,
    trace_id: str | None = None,
    request_key: str | None = None,
    resolved_entities: tuple[EntityReference, ...] = (),
) -> TurnAnalysis:
    """Create a bounded goal model before any memory retrieval or model prompt."""
    trace_id = trace_id or uuid.uuid4().hex
    decisions, preserve_order, decomposition = _compound_operational_plan(
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
        request_key=request_key,
    )
    return TurnAnalysis(
        state,
        effective_text,
        tuple(decisions),
        preserve_order,
        recognize_deterministic(effective_text),
        decomposition,
    )
