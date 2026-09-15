"""Typed operational planning and execution for one conversational turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence
import uuid

from agent.orchestration.contracts import ResponseCandidate
from agent.routing import RoutingDecision
from .contracts import TurnState


@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str
    decision: RoutingDecision
    depends_on: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str | None = None

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
        }


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    id: str
    turn_id: str
    steps: tuple[PlanStep, ...]
    execution_mode: str
    risk: str
    approval_strategy: str

    def __post_init__(self) -> None:
        if self.execution_mode not in {"sequential", "parallel_read_only"}:
            raise ValueError("Invalid execution mode")
        if self.risk not in {"none", "read_only", "mutating"}:
            raise ValueError("Invalid plan risk")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "id": self.id,
            "turn_id": self.turn_id,
            "execution_mode": self.execution_mode,
            "risk": self.risk,
            "approval_strategy": self.approval_strategy,
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

    @property
    def failed(self) -> bool:
        return self.status in {"failed", "rejected", "skipped_dependency"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability": self.capability,
            "status": self.status,
            "verification_status": self.verification_status,
            "retryable": self.retryable,
            "error_type": self.error_type,
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
) -> ExecutionPlan:
    """Validate a plan and parallelize only independent read-only work."""
    if not decisions:
        raise ValueError("An operational plan requires at least one decision")
    all_read_only = all(item.risk in {"none", "read_only"} for item in decisions)
    parallel = len(decisions) > 1 and all_read_only and not preserve_order
    steps: list[PlanStep] = []
    previous: str | None = None
    for decision in decisions:
        available, reason = True, None
        if decision.selected_capability:
            try:
                from agent.tooling import get_runtime_registry

                definition = get_runtime_registry().get(decision.selected_capability)
                available, reason = definition.availability()
            except KeyError:
                available, reason = False, "Capability is not registered"
        step_id = uuid.uuid4().hex[:12]
        steps.append(
            PlanStep(
                step_id,
                decision,
                () if parallel or not previous else (previous,),
                available,
                reason,
            )
        )
        previous = step_id
    risk = (
        "mutating"
        if any(item.risk == "mutating" for item in decisions)
        else "read_only"
    )
    return ExecutionPlan(
        id=uuid.uuid4().hex,
        turn_id=state.id,
        steps=tuple(steps),
        execution_mode="parallel_read_only" if parallel else "sequential",
        risk=risk,
        approval_strategy=(
            "per_consequential_step"
            if any(item.approval_required for item in decisions)
            else "preauthorized_policy"
        ),
    )


ExecuteDecision = Callable[[RoutingDecision], Awaitable[ResponseCandidate | None]]


class PlanExecutor:
    """Execute typed steps while preserving partial outcomes and dependencies."""

    @staticmethod
    async def _run(step: PlanStep, execute: ExecuteDecision) -> StepOutcome:
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
            candidate = await execute(step.decision)
            if candidate is None:
                raise RuntimeError("No executor accepted the planned step")
            metadata = dict(candidate.metadata or {})
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
                str(metadata.get("error_type")) if metadata.get("error_type") else None,
                metadata,
            )
        except Exception as exc:
            return StepOutcome(
                step.id,
                step.capability,
                "failed",
                "failed",
                f"I couldn't complete {step.capability.replace('_', ' ')}. Nothing else dependent on it ran.",
                "planner:execution_error",
                retryable=bool(getattr(exc, "retryable", False)),
                error_type=type(exc).__name__,
            )

    async def execute(
        self, plan: ExecutionPlan, execute: ExecuteDecision
    ) -> PlanExecutionResult:
        if plan.execution_mode == "parallel_read_only":
            outcomes = tuple(
                await asyncio.gather(*(self._run(step, execute) for step in plan.steps))
            )
        else:
            completed: set[str] = set()
            collected: list[StepOutcome] = []
            for step in plan.steps:
                if any(dependency not in completed for dependency in step.depends_on):
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
                outcome = await self._run(step, execute)
                collected.append(outcome)
                if not outcome.failed and outcome.status not in {
                    "waiting_approval",
                    "clarification",
                }:
                    completed.add(step.id)
            outcomes = tuple(collected)

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
        status = waiting or (
            "partial" if failed and succeeded else "failed" if failed else "completed"
        )
        return PlanExecutionResult(plan, status, outcomes)
