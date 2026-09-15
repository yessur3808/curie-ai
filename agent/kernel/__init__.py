"""Typed turn-understanding kernel used by every conversational connector."""

from .contracts import (
    EntityReference,
    GoalConstraint,
    GoalSpec,
    ResponseMode,
    SubGoal,
    TurnState,
)

__all__ = [
    "EntityReference",
    "GoalConstraint",
    "GoalSpec",
    "ResponseMode",
    "SubGoal",
    "TurnState",
]
