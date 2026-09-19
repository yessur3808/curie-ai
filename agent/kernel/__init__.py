"""Typed turn-understanding kernel used by every conversational connector."""

from .contracts import (
    EntityReference,
    GoalConstraint,
    GoalSpec,
    ResponseMode,
    SubGoal,
    TurnState,
)
from .context import (
    ContextBudgeter,
    ContextCandidate,
    ContextDecision,
    ContextEnvelope,
    ContextSection,
)
from .dialogue_state import (
    DialogueState,
    DialogueStateStore,
    DialogueTransition,
    StateValue,
    TransitionClass,
)
from .errors import PipelineError, PipelineErrorKind
from .feature_flags import PipelineFeatureFlags, PipelineMode
from .inbound import (
    AttachmentDescriptor,
    EditedMessagePolicy,
    InboundEvent,
    InboundEventError,
    InboundEventKind,
)
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
    "AttachmentDescriptor",
    "ContextBudgeter",
    "ContextCandidate",
    "ContextDecision",
    "ContextEnvelope",
    "ContextSection",
    "DialogueState",
    "DialogueStateStore",
    "DialogueTransition",
    "EditedMessagePolicy",
    "InboundEvent",
    "InboundEventError",
    "InboundEventKind",
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
    "StateValue",
    "TransitionClass",
    "TurnPipeline",
]
