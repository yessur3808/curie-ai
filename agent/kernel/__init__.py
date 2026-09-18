"""Typed turn-understanding kernel used by every conversational connector."""

from .contracts import (
    EntityReference,
    GoalConstraint,
    GoalSpec,
    ResponseMode,
    SubGoal,
    TurnState,
)
from .errors import PipelineError, PipelineErrorKind
from .feature_flags import PipelineFeatureFlags, PipelineMode
from .pipeline import (
    PIPELINE_STAGE_ORDER,
    PipelineStage,
    PipelineState,
    SideEffect,
    StageOutput,
    StageResult,
    StageStatus,
    TurnPipeline,
)

__all__ = [
    "EntityReference",
    "GoalConstraint",
    "GoalSpec",
    "ResponseMode",
    "SubGoal",
    "TurnState",
    "PIPELINE_STAGE_ORDER",
    "PipelineError",
    "PipelineErrorKind",
    "PipelineFeatureFlags",
    "PipelineMode",
    "PipelineStage",
    "PipelineState",
    "SideEffect",
    "StageOutput",
    "StageResult",
    "StageStatus",
    "TurnPipeline",
]
