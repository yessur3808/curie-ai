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
from .cancellation import CancellationRegistry, get_cancellation_registry
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
from .execution import (
    CompensationRequest,
    RetryDecision,
    decide_retry,
    stable_idempotency_key,
)
from .planning import (
    ExecutionPlan,
    PlanDependency,
    PlanExecutionResult,
    PlanExecutor,
    PlannerInput,
    PlanStep,
    StepOutcome,
    build_execution_plan,
)

__all__ = [
    "EntityReference",
    "ExecutionPlan",
    "GoalConstraint",
    "GoalSpec",
    "ResponseMode",
    "SubGoal",
    "TurnState",
    "AttachmentDescriptor",
    "CancellationRegistry",
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
    "PlanDependency",
    "PlanExecutionResult",
    "PlanExecutor",
    "PlannerInput",
    "PlanStep",
    "SideEffect",
    "StageOutput",
    "StageResult",
    "StageStatus",
    "StateValue",
    "StepOutcome",
    "TransitionClass",
    "TurnPipeline",
    "CompensationRequest",
    "RetryDecision",
    "build_execution_plan",
    "decide_retry",
    "stable_idempotency_key",
    "get_cancellation_registry",
]
