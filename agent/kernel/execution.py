"""Idempotency, retry, cancellation, and compensation primitives."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


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
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


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
    if cancellation_requested:
        return RetryDecision(False, "user_cancelled")
    if attempt >= max_attempts:
        return RetryDecision(False, "attempt_limit")
    if isinstance(error, (ValueError, PermissionError, LookupError)):
        return RetryDecision(False, "non_retryable_input_or_policy")
    retryable = bool(getattr(error, "retryable", False)) or isinstance(
        error, (TimeoutError, ConnectionError)
    )
    if not retryable:
        return RetryDecision(False, "error_not_transient")
    if not read_only and idempotency_mode not in {
        "state_reconciled",
        "provider_key",
    }:
        return RetryDecision(False, "mutation_not_safely_idempotent")
    retry_after = getattr(error, "retry_after", 0.0)
    try:
        delay = min(max(float(retry_after), 0.0), 5.0)
    except (TypeError, ValueError):
        delay = 0.0
    return RetryDecision(True, "transient_safe_retry", delay)


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
