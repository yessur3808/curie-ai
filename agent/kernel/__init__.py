"""Typed turn-understanding kernel used by every conversational connector."""

from .contracts import (
    EntityReference,
    GoalSpec,
    ResponseMode,
    SubGoal,
    TurnState,
)

__all__ = [
    "EntityReference",
    "GoalSpec",
    "ResponseMode",
    "SubGoal",
    "TurnState",
]
