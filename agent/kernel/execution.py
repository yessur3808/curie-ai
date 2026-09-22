"""Idempotency, retry, cancellation, and compensation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent import task_engine


def stable_idempotency_key(
    *,
    owner_scope_hash: str,
    plan_hash: str,
    step_id: str,
    target: Any,
    desired_state: Any,
) -> str:
    payload = {
        "owner": owner_scope_hash,
        "plan": plan_hash,
        "step": step_id,
        "target": target,
        "desired_state": desired_state,
    }
    return task_engine.stable_key(payload)


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    reason: str
    delay_seconds: float = 0.0


def decide_retry(
    *,
    error: BaseException,
    attempt: int,
    max_attempts: int,
    read_only: bool,
    idempotency_mode: str,
    cancellation_requested: bool = False,
) -> RetryDecision:
    if isinstance(error, ValueError):
        error_kind = "value"
    elif isinstance(error, PermissionError):
        error_kind = "permission"
    elif isinstance(error, LookupError):
        error_kind = "lookup"
    elif isinstance(error, TimeoutError):
        error_kind = "timeout"
    elif isinstance(error, ConnectionError):
        error_kind = "connection"
    else:
        error_kind = "other"
    retry_after = getattr(error, "retry_after", 0.0)
    try:
        retry_after_ms = round(float(retry_after) * 1000)
    except (TypeError, ValueError):
        retry_after_ms = 0
    retry, reason, delay_ms = task_engine.retry_decision(
        attempt=attempt,
        max_attempts=max_attempts,
        read_only=read_only,
        idempotency_mode=idempotency_mode,
        error_kind=error_kind,
        explicit_retryable=bool(getattr(error, "retryable", False)),
        retry_after_ms=retry_after_ms,
        cancellation_requested=cancellation_requested,
    )
    return RetryDecision(retry, reason, delay_ms / 1000)


@dataclass(frozen=True, slots=True)
class CompensationRequest:
    original_step_id: str
    capability: str
    params: Mapping[str, Any]
    pre_state: Mapping[str, Any]
    approval_required: bool = True

    @property
    def explanation(self) -> str:
        return (
            "Compensation is a new action intended to restore captured pre-state; "
            "it does not erase the original action or its outcome."
        )
