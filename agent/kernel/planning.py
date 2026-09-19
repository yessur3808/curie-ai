"""Typed operational planning and execution for one conversational turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from typing import Any, Awaitable, Callable, Mapping, Sequence

from agent.orchestration.contracts import ResponseCandidate
from agent.routing import RoutingDecision
from .contracts import TurnState
from .execution import CompensationRequest, decide_retry, stable_idempotency_key


@dataclass(frozen=True, slots=True)
class PlannerInput:
    understanding: TurnState
    capability_snapshot: tuple[Mapping[str, Any], ...]
    dialogue_state: Mapping[str, Any] = field(default_factory=dict)
    relevant_memory: tuple[Mapping[str, Any], ...] = ()
    owner_permissions: frozenset[str] = frozenset()
    operational_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        forbidden = {"secret", "token", "password", "raw_update", "provider_response"}
        for source in (self.dialogue_state, *self.relevant_memory):
            if any(
                fragment in str(key).casefold()
                for key in source
                for fragment in forbidden
            ):
                raise ValueError(
                    "Planner input contains a forbidden raw or secret field"
                )


@dataclass(frozen=True, slots=True)
class PlanDependency:
    step_id: str
    kind: str = "requested_order"

    def __post_init__(self) -> None:
        if self.kind not in {"requested_order", "data_dependency"}:
            raise ValueError("Invalid plan dependency kind")


@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str
    decision: RoutingDecision
    depends_on: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str | None = None
    dependencies: tuple[PlanDependency, ...] = ()
    expected_latency_seconds: float = 0.0
    possible_partial_outcomes: tuple[str, ...] = ()
    idempotency_key: str = ""
    max_attempts: int = 1
    verification: Mapping[str, Any] = field(default_factory=dict)
    compensation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        dependency_ids = tuple(item.step_id for item in self.dependencies)
        if self.dependencies and dependency_ids != self.depends_on:
            raise ValueError("Typed dependencies must match depends_on")
        if self.max_attempts < 1:
            raise ValueError("Plan steps require at least one attempt")

    @property
    def capability(self) -> str:
        return self.decision.selected_capability or str(
            self.decision.parameters.get("command") or self.decision.intent
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "decision_id": self.decision.id,
            "intent": self.decision.intent,
            "capability": self.capability,
            "risk": self.decision.risk,
            "depends_on": list(self.depends_on),
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "dependencies": [
                {"step_id": item.step_id, "kind": item.kind}
                for item in self.dependencies
            ],
            "expected_latency_seconds": self.expected_latency_seconds,
            "possible_partial_outcomes": list(self.possible_partial_outcomes),
            "idempotency_key": self.idempotency_key,
            "max_attempts": self.max_attempts,
            "verification": dict(self.verification),
            "compensation": dict(self.compensation),
        }


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    id: str
    turn_id: str
    steps: tuple[PlanStep, ...]
    execution_mode: str
    risk: str
    approval_strategy: str
    plan_hash: str = ""
    owner_scope_hash: str = ""
    connector: str = "unknown"
    approval_groups: tuple[tuple[str, ...], ...] = ()
    readable_preview: str = ""
    expected_latency_seconds: float = 0.0
    possible_partial_outcomes: tuple[str, ...] = ()
    expires_at: str | None = None

    def __post_init__(self) -> None:
        if self.execution_mode not in {"sequential", "parallel_read_only"}:
            raise ValueError("Invalid execution mode")
        if self.risk not in {"none", "read_only", "mutating"}:
            raise ValueError("Invalid plan risk")
        known = {item.id for item in self.steps}
        if len(known) != len(self.steps):
            raise ValueError("Plan step IDs must be unique")
        graph = {item.id: set(item.depends_on) for item in self.steps}
        if any(not dependencies <= known for dependencies in graph.values()):
            raise ValueError("Plan dependencies must reference existing steps")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Plan dependency graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in graph[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "id": self.id,
            "turn_id": self.turn_id,
            "execution_mode": self.execution_mode,
            "risk": self.risk,
            "approval_strategy": self.approval_strategy,
            "plan_hash": self.plan_hash,
            "owner_scope_hash": self.owner_scope_hash,
            "connector": self.connector,
            "approval_groups": [list(item) for item in self.approval_groups],
            "readable_preview": self.readable_preview,
            "expected_latency_seconds": self.expected_latency_seconds,
            "possible_partial_outcomes": list(self.possible_partial_outcomes),
            "expires_at": self.expires_at,
            "steps": [item.as_dict() for item in self.steps],
        }


@dataclass(frozen=True, slots=True)
class StepOutcome:
    step_id: str
    capability: str
    status: str
    verification_status: str
    text: str
    model_used: str
    retryable: bool = False
    error_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    attempts: int = 1

    @property
    def failed(self) -> bool:
        return self.status in {"failed", "rejected", "skipped_dependency"}

    @property
    def cancelled(self) -> bool:
        return self.status in {"cancelled", "cancelled_before_start"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability": self.capability,
            "status": self.status,
            "verification_status": self.verification_status,
            "retryable": self.retryable,
            "error_type": self.error_type,
            "attempts": self.attempts,
        }


@dataclass(frozen=True, slots=True)
class PlanExecutionResult:
    plan: ExecutionPlan
    status: str
    outcomes: tuple[StepOutcome, ...]

    @property
    def message_parts(self) -> list[str]:
        return [item.text for item in self.outcomes if item.text.strip()]

    @property
    def text(self) -> str:
        return "\n\n".join(self.message_parts)

    @property
    def model_used(self) -> str:
        return "+".join(
            dict.fromkeys(item.model_used for item in self.outcomes if item.model_used)
        )

    @property
    def verification_status(self) -> str:
        values = {item.verification_status for item in self.outcomes}
        if "failed" in values:
            return "failed"
        if values & {"contradicted", "unverified"}:
            return "unverified"
        if values == {"not_required"}:
            return "not_required"
        if values and values <= {
            "verified",
            "already_satisfied",
            "not_required",
        }:
            return "verified"
        if "completed_unverified" in values:
            return "completed_unverified"
        return "not_required"

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.as_dict(),
            "status": self.status,
            "verification_status": self.verification_status,
            "outcomes": [item.as_dict() for item in self.outcomes],
        }


def build_execution_plan(
    state: TurnState,
    decisions: Sequence[RoutingDecision],
    *,
    preserve_order: bool = True,
    dependency_kinds: Mapping[int, str] | None = None,
) -> ExecutionPlan:
    """Validate a plan and parallelize only independent read-only work."""
    if not decisions:
        raise ValueError("An operational plan requires at least one decision")
    # Keep the planner boundary deliberately narrow: it receives typed
    # understanding and public capability policy, never executors, credentials,
    # or raw provider responses.
    try:
        from agent.tooling import get_runtime_registry

        capability_snapshot = tuple(
            {
                "name": definition.name,
                "risk": definition.risk,
                "available": definition.available,
                "required_permissions": sorted(definition.required_permissions),
            }
            for definition in get_runtime_registry().all()
        )
    except Exception:
        capability_snapshot = ()
    planner_input = PlannerInput(state, capability_snapshot)
    serialized = [
        {
            "intent": item.intent,
            "capability": item.selected_capability,
            "parameters": dict(item.parameters),
            "risk": item.risk,
            "approval_required": item.approval_required,
        }
        for item in decisions
    ]
    plan_hash = hashlib.sha256(
        json.dumps(
            {
                "owner_scope_hash": planner_input.understanding.owner_scope_hash,
                "connector": planner_input.understanding.platform,
                "request_key_hash": planner_input.understanding.request_key_hash,
                "steps": serialized,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    all_read_only = all(item.risk in {"none", "read_only"} for item in decisions)
    parallel = len(decisions) > 1 and all_read_only and not preserve_order
    steps: list[PlanStep] = []
    previous: str | None = None
    approval_ttl = max(1, int(os.getenv("CURIE_APPROVAL_TTL_MINUTES", "30")))
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=approval_ttl)
    ).isoformat()
    for index, decision in enumerate(decisions):
        available, reason = True, None
        definition = None
        if decision.selected_capability:
            try:
                from agent.tooling import get_runtime_registry

                definition = get_runtime_registry().get(decision.selected_capability)
                available, reason = definition.availability()
            except KeyError:
                available, reason = False, "Capability is not registered"
        step_id = hashlib.sha256(
            f"{plan_hash}:{index}:{decision.selected_capability or decision.intent}".encode()
        ).hexdigest()[:12]
        depends_on = () if parallel or not previous else (previous,)
        dependency_kind = (dependency_kinds or {}).get(index, "requested_order")
        authorization = {
            "plan_hash": plan_hash,
            "step_id": step_id,
            "owner_scope_hash": state.owner_scope_hash,
            "connector": state.platform,
            "parameter_hash": hashlib.sha256(
                json.dumps(
                    dict(decision.parameters),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest(),
            "expires_at": expires_at,
        }
        planned_decision = replace(decision, authorization=authorization)
        idempotency_key = stable_idempotency_key(
            owner_scope_hash=state.owner_scope_hash,
            plan_hash=plan_hash,
            step_id=step_id,
            target=(
                decision.parameters.get("target")
                or decision.parameters.get("targets")
                or decision.parameters.get("recipient")
                or decision.parameters.get("participant_id")
                or ""
            ),
            desired_state=(
                decision.parameters.get("state")
                or decision.parameters.get("text")
                or decision.parameters.get("request")
                or ""
            ),
        )
        planned_decision = replace(
            planned_decision,
            authorization={
                **authorization,
                "idempotency_key": idempotency_key,
            },
        )
        verification = (
            {
                "supported": definition.verification.supported,
                "query_capability": definition.verification.query_capability,
                "expected_state_projection": definition.verification.expected_state_projection,
                "maximum_consistency_delay_seconds": definition.verification.maximum_consistency_delay_seconds,
                "contradiction_behavior": definition.verification.contradiction_behavior,
                "fallback": definition.verification.fallback,
            }
            if definition
            else {}
        )
        compensation = (
            {
                "capability": definition.compensation.capability,
                "captures_pre_state": definition.compensation.captures_pre_state,
                "approval_required": definition.compensation.approval_required,
            }
            if definition
            else {}
        )
        steps.append(
            PlanStep(
                step_id,
                planned_decision,
                depends_on,
                available,
                reason,
                tuple(PlanDependency(item, dependency_kind) for item in depends_on),
                (
                    float(definition.resource_policy.timeout_seconds)
                    if definition
                    else 0.0
                ),
                (
                    "completed",
                    "failed",
                    "cancelled_before_start",
                    "completed_unverified",
                ),
                idempotency_key,
                (2 if definition and definition.idempotency.safe_retry else 1),
                verification,
                compensation,
            )
        )
        previous = step_id
    risk = (
        "mutating"
        if any(item.risk == "mutating" for item in decisions)
        else "read_only"
    )
    approval_groups: dict[str, list[str]] = {}
    for step in steps:
        if step.decision.approval_required:
            approval_groups.setdefault(step.capability, []).append(step.id)
    preview = " → ".join(
        f"{index}. {step.capability.replace('_', ' ')} ({step.decision.risk})"
        for index, step in enumerate(steps, 1)
    )
    return ExecutionPlan(
        id=plan_hash[:32],
        turn_id=state.id,
        steps=tuple(steps),
        execution_mode="parallel_read_only" if parallel else "sequential",
        risk=risk,
        approval_strategy=(
            "per_consequential_step"
            if any(item.approval_required for item in decisions)
            else "preauthorized_policy"
        ),
        plan_hash=plan_hash,
        owner_scope_hash=state.owner_scope_hash,
        connector=state.platform,
        approval_groups=tuple(tuple(items) for items in approval_groups.values()),
        readable_preview=preview,
        expected_latency_seconds=(
            max((item.expected_latency_seconds for item in steps), default=0.0)
            if parallel
            else sum(item.expected_latency_seconds for item in steps)
        ),
        possible_partial_outcomes=(
            "all_completed",
            "partial_completion",
            "waiting_approval",
            "cancelled_after_partial_completion",
        ),
        expires_at=expires_at,
    )


ExecuteDecision = Callable[[RoutingDecision], Awaitable[ResponseCandidate | None]]


class PlanExecutor:
    """Execute typed steps while preserving partial outcomes and dependencies."""

    @staticmethod
    async def _run(
        step: PlanStep,
        execute: ExecuteDecision,
        cancellation_event: asyncio.Event | None = None,
    ) -> StepOutcome:
        if cancellation_event and cancellation_event.is_set():
            return StepOutcome(
                step.id,
                step.capability,
                "cancelled_before_start",
                "not_run",
                f"I didn't run {step.capability.replace('_', ' ')} because the task was cancelled.",
                "planner:cancellation",
                attempts=0,
            )
        if not step.available:
            return StepOutcome(
                step.id,
                step.capability,
                "failed",
                "failed",
                f"{step.capability.replace('_', ' ').title()} is unavailable: "
                f"{step.unavailable_reason or 'not configured'}.",
                "planner:unavailable",
                error_type="capability_unavailable",
            )
        try:
            from agent.tooling import get_runtime_registry

            definition = get_runtime_registry().get(step.capability)
            read_only = definition.risk == "read_only"
            idempotency_mode = definition.idempotency.mode
        except KeyError:
            read_only = step.decision.risk != "mutating"
            idempotency_mode = "read_only" if read_only else "none"
        last_error: Exception | None = None
        for attempt in range(1, step.max_attempts + 1):
            if cancellation_event and cancellation_event.is_set():
                return StepOutcome(
                    step.id,
                    step.capability,
                    "cancelled_before_start",
                    "not_run",
                    f"I didn't run {step.capability.replace('_', ' ')} because the task was cancelled.",
                    "planner:cancellation",
                    attempts=attempt - 1,
                )
            try:
                candidate = await execute(step.decision)
                if candidate is None:
                    raise RuntimeError("No executor accepted the planned step")
                metadata = dict(candidate.metadata or {})
                if str(metadata.get("status") or "") == "failed" and bool(
                    metadata.get("retryable")
                ):
                    retry = decide_retry(
                        error=ConnectionError(
                            "Capability reported a transient failure"
                        ),
                        attempt=attempt,
                        max_attempts=step.max_attempts,
                        read_only=read_only,
                        idempotency_mode=idempotency_mode,
                        cancellation_requested=bool(
                            cancellation_event and cancellation_event.is_set()
                        ),
                    )
                    if retry.retry:
                        if retry.delay_seconds:
                            await asyncio.sleep(retry.delay_seconds)
                        continue
                if cancellation_event and cancellation_event.is_set():
                    metadata["cancellation_observed_after_completion"] = True
                return StepOutcome(
                    step.id,
                    step.capability,
                    str(metadata.get("status") or "completed"),
                    str(
                        metadata.get("verification_status")
                        or (
                            "completed_unverified"
                            if step.decision.risk == "mutating"
                            else "not_required"
                        )
                    ),
                    candidate.text,
                    candidate.model_used,
                    bool(metadata.get("retryable", False)),
                    (
                        str(metadata.get("error_type"))
                        if metadata.get("error_type")
                        else None
                    ),
                    metadata,
                    attempt,
                )
            except Exception as exc:
                last_error = exc
                retry = decide_retry(
                    error=exc,
                    attempt=attempt,
                    max_attempts=step.max_attempts,
                    read_only=read_only,
                    idempotency_mode=idempotency_mode,
                    cancellation_requested=bool(
                        cancellation_event and cancellation_event.is_set()
                    ),
                )
                if not retry.retry:
                    break
                if retry.delay_seconds:
                    await asyncio.sleep(retry.delay_seconds)
        return StepOutcome(
            step.id,
            step.capability,
            "failed",
            "failed",
            f"I couldn't complete {step.capability.replace('_', ' ')}. Nothing else dependent on it ran.",
            "planner:execution_error",
            retryable=bool(getattr(last_error, "retryable", False)),
            error_type=type(last_error).__name__ if last_error else "RuntimeError",
            attempts=step.max_attempts,
        )

    async def execute(
        self,
        plan: ExecutionPlan,
        execute: ExecuteDecision,
        *,
        cancellation_event: asyncio.Event | None = None,
        cleanup_callbacks: Sequence[Callable[[], Any]] = (),
    ) -> PlanExecutionResult:
        try:
            if plan.execution_mode == "parallel_read_only":
                outcomes = tuple(
                    await asyncio.gather(
                        *(
                            self._run(step, execute, cancellation_event)
                            for step in plan.steps
                        )
                    )
                )
            else:
                completed: set[str] = set()
                collected: list[StepOutcome] = []
                for step in plan.steps:
                    if cancellation_event and cancellation_event.is_set():
                        collected.append(
                            StepOutcome(
                                step.id,
                                step.capability,
                                "cancelled_before_start",
                                "not_run",
                                f"I didn't run {step.capability.replace('_', ' ')} because the task was cancelled.",
                                "planner:cancellation",
                                attempts=0,
                            )
                        )
                        continue
                    if any(
                        dependency not in completed for dependency in step.depends_on
                    ):
                        collected.append(
                            StepOutcome(
                                step.id,
                                step.capability,
                                "skipped_dependency",
                                "not_run",
                                f"I didn't run {step.capability.replace('_', ' ')} because an earlier step failed.",
                                "planner:dependency",
                            )
                        )
                        continue
                    outcome = await self._run(step, execute, cancellation_event)
                    collected.append(outcome)
                    if (
                        not outcome.failed
                        and not outcome.cancelled
                        and outcome.status
                        not in {
                            "waiting_approval",
                            "clarification",
                        }
                    ):
                        completed.add(step.id)
                outcomes = tuple(collected)
        finally:
            for callback in cleanup_callbacks:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result

        failed = [item for item in outcomes if item.failed]
        succeeded = [
            item
            for item in outcomes
            if not item.failed
            and item.status not in {"waiting_approval", "clarification"}
        ]
        waiting = next(
            (
                item.status
                for item in outcomes
                if item.status in {"waiting_approval", "clarification"}
            ),
            None,
        )
        cancelled = [item for item in outcomes if item.cancelled]
        status = waiting or (
            "partial_cancelled"
            if cancelled and succeeded
            else (
                "cancelled"
                if cancelled
                else (
                    "partial"
                    if failed and succeeded
                    else "failed" if failed else "completed"
                )
            )
        )
        return PlanExecutionResult(plan, status, outcomes)

    @staticmethod
    def compensation_requests(
        result: PlanExecutionResult,
    ) -> tuple[CompensationRequest, ...]:
        """Describe rollback-like actions without pretending history is erased."""
        by_id = {step.id: step for step in result.plan.steps}
        requests: list[CompensationRequest] = []
        for outcome in reversed(result.outcomes):
            step = by_id.get(outcome.step_id)
            if not step or outcome.failed or outcome.cancelled:
                continue
            capability = str(step.compensation.get("capability") or "")
            result_data = outcome.metadata.get("data")
            pre_state = outcome.metadata.get("pre_state")
            if not isinstance(pre_state, Mapping) and isinstance(result_data, Mapping):
                pre_state = result_data.get("pre_state")
            if not capability or not isinstance(pre_state, Mapping):
                continue
            requests.append(
                CompensationRequest(
                    step.id,
                    capability,
                    dict(pre_state),
                    dict(pre_state),
                    bool(step.compensation.get("approval_required", True)),
                )
            )
        return tuple(requests)
