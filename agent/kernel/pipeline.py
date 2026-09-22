"""Typed, immutable stage runner for one Curie turn.

The runner deliberately knows nothing about Telegram, models, databases, or
specific tools.  Adapters provide handlers while this module enforces order,
timeouts, cancellation, side-effect declarations, and privacy-safe traces.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import inspect
import re
import time
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping
import uuid

from .errors import PipelineError, PipelineErrorKind
from .feature_flags import PipelineMode
from utils.redaction import redact_secrets


class PipelineStage(str, Enum):
    NORMALIZE = "normalize"
    RESOLVE_IDENTITY = "resolve_identity"
    BUILD_CONTEXT = "build_context"
    UNDERSTAND = "understand"
    ROUTE = "route"
    PLAN = "plan"
    AUTHORIZE = "authorize"
    EXECUTE = "execute"
    VERIFY = "verify"
    PLAN_RESPONSE = "plan_response"
    RENDER_RESPONSE = "render_response"
    DELIVER = "deliver"
    PERSIST = "persist"
    RECORD_LEARNING = "record_learning"


PIPELINE_STAGE_ORDER = tuple(PipelineStage)


class StageStatus(str, Enum):
    COMPLETED = "completed"
    DEGRADED = "degraded"
    DEFERRED = "deferred"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class SideEffect(str, Enum):
    IDENTITY_WRITE = "identity_write"
    STORAGE_READ = "storage_read"
    APPROVAL_CONTROL = "approval_control"
    TOOL_READ = "tool_read"
    TOOL_MUTATION = "tool_mutation"
    DELIVERY = "delivery"
    PERSISTENCE = "persistence"
    LEARNING = "learning"
    LEGACY_BRIDGE = "legacy_bridge"


class FailureBehavior(str, Enum):
    STOP = "stop"
    DEGRADE = "degrade"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class StagePolicy:
    stage: PipelineStage
    timeout_seconds: float
    input_type: str
    output_type: str
    allowed_side_effects: frozenset[SideEffect] = frozenset()
    failure_behavior: FailureBehavior = FailureBehavior.STOP
    legacy_fallback_allowed: bool = False
    cancellation_behavior: str = "cancel_before_stage_or_at_timeout"
    trace_fields: tuple[str, ...] = (
        "stage",
        "trace_id",
        "turn_id",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "effects",
        "error",
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "timeout_seconds": self.timeout_seconds,
            "input_type": self.input_type,
            "output_type": self.output_type,
            "allowed_side_effects": sorted(
                item.value for item in self.allowed_side_effects
            ),
            "failure_behavior": self.failure_behavior.value,
            "legacy_fallback_allowed": self.legacy_fallback_allowed,
            "cancellation_behavior": self.cancellation_behavior,
            "trace_fields": list(self.trace_fields),
        }


DEFAULT_STAGE_POLICIES: tuple[StagePolicy, ...] = (
    StagePolicy(PipelineStage.NORMALIZE, 1.0, "NormalizedInput", "CanonicalTurnInput"),
    StagePolicy(
        PipelineStage.RESOLVE_IDENTITY,
        5.0,
        "CanonicalTurnInput",
        "OwnerScopedTurn",
        frozenset({SideEffect.IDENTITY_WRITE}),
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.BUILD_CONTEXT,
        10.0,
        "OwnerScopedTurn",
        "ContextEnvelope",
        frozenset({SideEffect.STORAGE_READ}),
        FailureBehavior.DEGRADE,
        True,
    ),
    StagePolicy(
        PipelineStage.UNDERSTAND,
        10.0,
        "ContextEnvelope",
        "TurnAnalysis",
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.ROUTE,
        10.0,
        "TurnAnalysis",
        "RoutingDecision",
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.PLAN,
        5.0,
        "RoutingDecision",
        "ExecutionPlan",
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.AUTHORIZE,
        10.0,
        "ExecutionPlan",
        "AuthorizationDecision",
        frozenset({SideEffect.APPROVAL_CONTROL}),
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.EXECUTE,
        180.0,
        "AuthorizationDecision",
        "ExecutionResult",
        frozenset(
            {
                SideEffect.TOOL_READ,
                SideEffect.TOOL_MUTATION,
                SideEffect.PERSISTENCE,
                SideEffect.LEARNING,
                SideEffect.LEGACY_BRIDGE,
            }
        ),
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.VERIFY,
        30.0,
        "ExecutionResult",
        "VerifiedOutcome",
        frozenset({SideEffect.TOOL_READ}),
        FailureBehavior.DEGRADE,
        True,
    ),
    StagePolicy(
        PipelineStage.PLAN_RESPONSE,
        5.0,
        "VerifiedOutcome",
        "ResponsePlan",
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.RENDER_RESPONSE,
        30.0,
        "ResponsePlan",
        "RenderedResponse",
        failure_behavior=FailureBehavior.FALLBACK,
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.DELIVER,
        30.0,
        "RenderedResponse",
        "DeliveryReceipt",
        frozenset({SideEffect.DELIVERY}),
        legacy_fallback_allowed=True,
    ),
    StagePolicy(
        PipelineStage.PERSIST,
        10.0,
        "DeliveryReceipt",
        "PersistenceReceipt",
        frozenset({SideEffect.PERSISTENCE}),
        FailureBehavior.DEGRADE,
        True,
    ),
    StagePolicy(
        PipelineStage.RECORD_LEARNING,
        5.0,
        "PersistenceReceipt",
        "LearningReceipt",
        frozenset({SideEffect.LEARNING}),
        FailureBehavior.DEGRADE,
        True,
    ),
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """Return ordinary containers for a connector-facing result."""
    if isinstance(value, Mapping):
        return {str(key): thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [thaw(item) for item in value]
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|passwd|secret|session|token|"
    r"api[_-]?key|private[_-]?key|raw[_-]?(?:message|text|content|parameters?))",
    re.I,
)
_TOKEN_LIKE_VALUE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:bearer\s+)?[A-Za-z0-9_-]{24,}(?:\.[A-Za-z0-9_-]{8,})?"
    r"(?:$|[^A-Za-z0-9])",
    re.I,
)
_SAFE_PLATFORM = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def _safe_trace_id(value: object) -> str:
    """Accept UUID-shaped upstream traces; replace all other values."""
    candidate = str(value or "").strip()
    try:
        return uuid.UUID(candidate).hex if candidate else uuid.uuid4().hex
    except (AttributeError, ValueError):
        return uuid.uuid4().hex


def _safe_platform(value: object) -> str:
    candidate = str(value or "unknown").strip().casefold()
    return candidate if _SAFE_PLATFORM.fullmatch(candidate) else "unknown"


def _redact_safe_value(value: Any, *, key: str = "") -> Any:
    """Defense-in-depth redaction for data declared safe by stage adapters."""
    if _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_safe_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_redact_safe_value(item) for item in value]
    if isinstance(value, str) and _TOKEN_LIKE_VALUE.search(value):
        return "[redacted]"
    return redact_secrets(value, key)


@dataclass(frozen=True, slots=True)
class StageOutput:
    artifact: Any = None
    safe_summary: Mapping[str, Any] = field(default_factory=dict)
    effects: frozenset[SideEffect] = frozenset()
    status: StageStatus = StageStatus.COMPLETED

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact", _freeze(self.artifact))
        object.__setattr__(self, "safe_summary", _freeze(self.safe_summary))
        object.__setattr__(self, "effects", frozenset(self.effects))


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: PipelineStage
    trace_id: str
    turn_id: str
    status: StageStatus
    started_at: str
    finished_at: str
    duration_ms: float
    effects: frozenset[SideEffect] = frozenset()
    safe_summary: Mapping[str, Any] = field(default_factory=dict)
    error: PipelineError | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "effects", frozenset(self.effects))
        object.__setattr__(self, "safe_summary", _freeze(self.safe_summary))

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "trace_id": self.trace_id,
            "turn_id": self.turn_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "effects": sorted(item.value for item in self.effects),
            "summary": _redact_safe_value(thaw(self.safe_summary)),
            "error": self.error.as_dict() if self.error else None,
        }


@dataclass(frozen=True, slots=True)
class PipelineState:
    trace_id: str
    turn_id: str
    mode: PipelineMode
    platform: str
    owner_scope_hash: str
    message_hash: str
    message_chars: int
    stage_results: tuple[StageResult, ...] = ()
    artifacts: Mapping[PipelineStage, Any] = field(
        default_factory=dict, repr=False, compare=False
    )
    seed: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", MappingProxyType(dict(self.artifacts)))
        object.__setattr__(self, "seed", _freeze(self.seed))

    @classmethod
    def create(
        cls,
        normalized_input: Mapping[str, Any],
        mode: PipelineMode,
    ) -> "PipelineState":
        text = str(normalized_input.get("text") or "")
        owner = str(normalized_input.get("internal_id") or "anonymous")
        trace_id = _safe_trace_id(normalized_input.get("trace_id"))
        return cls(
            trace_id=trace_id,
            turn_id=uuid.uuid4().hex,
            mode=mode,
            platform=_safe_platform(normalized_input.get("platform")),
            owner_scope_hash=hashlib.sha256(owner.encode()).hexdigest()[:16],
            message_hash=hashlib.sha256(text.casefold().strip().encode()).hexdigest(),
            message_chars=len(text),
            seed=dict(normalized_input),
        )

    def artifact(self, stage: PipelineStage, default: Any = None) -> Any:
        return self.artifacts.get(stage, default)

    def with_stage(self, result: StageResult, artifact: Any = None) -> "PipelineState":
        if result.trace_id != self.trace_id or result.turn_id != self.turn_id:
            raise ValueError("Stage result does not belong to this pipeline turn")
        if len(self.stage_results) >= len(PIPELINE_STAGE_ORDER):
            raise ValueError("Pipeline already contains every canonical stage")
        expected = PIPELINE_STAGE_ORDER[len(self.stage_results)]
        if result.stage is not expected:
            raise ValueError(
                f"Expected stage {expected.value}, received {result.stage.value}"
            )
        artifacts = dict(self.artifacts)
        if artifact is not None:
            artifacts[result.stage] = artifact
        owner_hash = self.owner_scope_hash
        if result.stage is PipelineStage.RESOLVE_IDENTITY and isinstance(
            artifact, Mapping
        ):
            owner = str(artifact.get("internal_id") or "")
            if owner:
                owner_hash = hashlib.sha256(owner.encode()).hexdigest()[:16]
        return PipelineState(
            trace_id=self.trace_id,
            turn_id=self.turn_id,
            mode=self.mode,
            platform=self.platform,
            owner_scope_hash=owner_hash,
            message_hash=self.message_hash,
            message_chars=self.message_chars,
            stage_results=(*self.stage_results, result),
            artifacts=artifacts,
            seed=self.seed,
        )

    @property
    def failed_stage(self) -> str | None:
        for result in self.stage_results:
            if result.status in {StageStatus.FAILED, StageStatus.CANCELLED}:
                return result.stage.value
        return None

    def as_dict(self) -> dict[str, Any]:
        """Serialize metadata only; raw messages and artifacts are never included."""
        return {
            "schema_version": 1,
            "trace_id": self.trace_id,
            "turn_id": self.turn_id,
            "mode": self.mode.value,
            "platform": self.platform,
            "owner_scope_hash": self.owner_scope_hash,
            "message_hash": self.message_hash,
            "message_chars": self.message_chars,
            "failed_stage": self.failed_stage,
            "stages": [result.as_dict() for result in self.stage_results],
        }


StageHandler = Callable[[PipelineState], StageOutput | Awaitable[StageOutput]]
FallbackRenderer = Callable[[PipelineState], StageOutput | Awaitable[StageOutput]]


class TurnPipeline:
    """Run all turn stages in one fixed order with explicit policies."""

    def __init__(
        self,
        handlers: Mapping[PipelineStage, StageHandler],
        *,
        policies: tuple[StagePolicy, ...] = DEFAULT_STAGE_POLICIES,
        fallback_renderer: FallbackRenderer | None = None,
    ):
        self.handlers = dict(handlers)
        self.policies = tuple(policies)
        self.fallback_renderer = fallback_renderer
        stages = tuple(policy.stage for policy in self.policies)
        if stages != PIPELINE_STAGE_ORDER:
            raise ValueError(
                "Pipeline policies must cover every stage in canonical order"
            )
        missing = [
            stage.value for stage in PIPELINE_STAGE_ORDER if stage not in handlers
        ]
        if missing:
            raise ValueError(f"Missing pipeline handlers: {', '.join(missing)}")

    @staticmethod
    async def _call(handler: StageHandler, state: PipelineState) -> StageOutput:
        value = handler(state)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, StageOutput):
            raise TypeError("Pipeline handlers must return StageOutput")
        return value

    @staticmethod
    def _result(
        policy: StagePolicy,
        state: PipelineState,
        status: StageStatus,
        started_at: str,
        started_clock: float,
        *,
        output: StageOutput | None = None,
        error: PipelineError | None = None,
    ) -> StageResult:
        return StageResult(
            stage=policy.stage,
            trace_id=state.trace_id,
            turn_id=state.turn_id,
            status=status,
            started_at=started_at,
            finished_at=_utc_now(),
            duration_ms=round((time.perf_counter() - started_clock) * 1000, 2),
            effects=output.effects if output else frozenset(),
            safe_summary=output.safe_summary if output else {},
            error=error,
        )

    @staticmethod
    def _skipped_result(
        state: PipelineState, stage: PipelineStage, reason: str
    ) -> StageResult:
        now = _utc_now()
        return StageResult(
            stage=stage,
            trace_id=state.trace_id,
            turn_id=state.turn_id,
            status=StageStatus.SKIPPED,
            started_at=now,
            finished_at=now,
            duration_ms=0.0,
            safe_summary={"reason": reason},
        )

    async def run(
        self,
        normalized_input: Mapping[str, Any],
        *,
        mode: PipelineMode = PipelineMode.ACTIVE,
        cancellation_event: asyncio.Event | None = None,
    ) -> PipelineState:
        state = PipelineState.create(normalized_input, mode)
        for index, policy in enumerate(self.policies):
            if cancellation_event and cancellation_event.is_set():
                now = _utc_now()
                error = PipelineError(
                    PipelineErrorKind.CANCELLED,
                    policy.stage.value,
                    recoverable=True,
                )
                state = state.with_stage(
                    StageResult(
                        policy.stage,
                        state.trace_id,
                        state.turn_id,
                        StageStatus.CANCELLED,
                        now,
                        now,
                        0.0,
                        error=error,
                    )
                )
                for later in self.policies[index + 1 :]:
                    state = state.with_stage(
                        self._skipped_result(state, later.stage, "turn_cancelled")
                    )
                break

            started_at = _utc_now()
            started_clock = time.perf_counter()
            try:
                output = await asyncio.wait_for(
                    self._call(self.handlers[policy.stage], state),
                    timeout=policy.timeout_seconds,
                )
                forbidden = output.effects - policy.allowed_side_effects
                if forbidden:
                    names = ", ".join(sorted(item.value for item in forbidden))
                    raise RuntimeError(
                        f"Stage {policy.stage.value} declared forbidden effects: {names}"
                    )
                if SideEffect.TOOL_MUTATION in output.effects:
                    authorization = next(
                        (
                            item
                            for item in state.stage_results
                            if item.stage is PipelineStage.AUTHORIZE
                        ),
                        None,
                    )
                    if not authorization or authorization.status not in {
                        StageStatus.COMPLETED,
                        StageStatus.DEGRADED,
                    }:
                        raise RuntimeError(
                            "Mutating execution cannot run before authorization"
                        )
                result = self._result(
                    policy,
                    state,
                    output.status,
                    started_at,
                    started_clock,
                    output=output,
                )
                state = state.with_stage(result, output.artifact)
                if output.status in {StageStatus.FAILED, StageStatus.CANCELLED}:
                    for later in self.policies[index + 1 :]:
                        state = state.with_stage(
                            self._skipped_result(
                                state, later.stage, "prior_stage_failed"
                            )
                        )
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, asyncio.TimeoutError):
                    exc = TimeoutError(f"{policy.stage.value} timed out")
                error = PipelineError.from_exception(policy.stage.value, exc)
                if (
                    policy.failure_behavior is FailureBehavior.FALLBACK
                    and self.fallback_renderer
                ):
                    try:
                        fallback = self.fallback_renderer(state)
                        if inspect.isawaitable(fallback):
                            fallback = await fallback
                        if not isinstance(fallback, StageOutput):
                            raise TypeError("Fallback renderer must return StageOutput")
                        state = state.with_stage(
                            self._result(
                                policy,
                                state,
                                StageStatus.DEGRADED,
                                started_at,
                                started_clock,
                                output=fallback,
                                error=error,
                            ),
                            fallback.artifact,
                        )
                        continue
                    except asyncio.CancelledError:
                        raise
                    except Exception as fallback_exc:
                        error = PipelineError.from_exception(
                            policy.stage.value, fallback_exc
                        )
                if policy.failure_behavior is FailureBehavior.DEGRADE:
                    degraded = StageOutput(
                        safe_summary={"degraded": True},
                        status=StageStatus.DEGRADED,
                    )
                    state = state.with_stage(
                        self._result(
                            policy,
                            state,
                            StageStatus.DEGRADED,
                            started_at,
                            started_clock,
                            output=degraded,
                            error=error,
                        )
                    )
                    continue

                state = state.with_stage(
                    self._result(
                        policy,
                        state,
                        StageStatus.FAILED,
                        started_at,
                        started_clock,
                        error=error,
                    )
                )
                for later in self.policies[index + 1 :]:
                    state = state.with_stage(
                        self._skipped_result(state, later.stage, "prior_stage_failed")
                    )
                break
        return state


def pipeline_contract() -> dict[str, Any]:
    """Machine-readable stage contract used by docs, health, and evaluation."""
    return {
        "version": "1.0",
        "stages": [policy.as_dict() for policy in DEFAULT_STAGE_POLICIES],
        "fixed_order": True,
        "privacy_safe_serialization": True,
        "mutation_requires_authorization": True,
    }
