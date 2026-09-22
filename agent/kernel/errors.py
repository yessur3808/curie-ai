"""Typed, privacy-safe failures for Curie's turn pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PipelineErrorKind(str, Enum):
    INVALID_INPUT = "invalid_input"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    UNAVAILABLE_CAPABILITY = "unavailable_capability"
    PERMISSION_REQUIRED = "permission_required"
    PERMISSION_DENIED = "permission_denied"
    AUTHENTICATION_EXPIRED = "authentication_expired"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    PROVIDER_REJECTED = "provider_rejected"
    VERIFICATION_FAILED = "verification_failed"
    PARTIAL_FAILURE = "partial_failure"
    UNSAFE_REQUEST = "unsafe_request"
    INTERNAL_INVARIANT = "internal_invariant"


@dataclass(frozen=True, slots=True)
class PipelineError:
    """A stage failure whose serialized form never includes exception text."""

    kind: PipelineErrorKind
    stage: str
    retryable: bool = False
    recoverable: bool = False
    exception_type: str | None = None

    @classmethod
    def from_exception(cls, stage: str, exc: BaseException) -> "PipelineError":
        message = str(exc).casefold()
        if isinstance(exc, TimeoutError):
            kind, retryable = PipelineErrorKind.TIMEOUT, True
        elif "model unavailable" in message or "capability unavailable" in message:
            kind, retryable = PipelineErrorKind.UNAVAILABLE_CAPABILITY, True
        elif isinstance(exc, PermissionError) and "expired" in message:
            kind, retryable = PipelineErrorKind.AUTHENTICATION_EXPIRED, False
        elif "rate limit" in message or "too many requests" in message:
            kind, retryable = PipelineErrorKind.RATE_LIMITED, True
        elif "provider" in message and "reject" in message:
            kind, retryable = PipelineErrorKind.PROVIDER_REJECTED, False
        elif "verification" in message or "state mismatch" in message:
            kind, retryable = PipelineErrorKind.VERIFICATION_FAILED, True
        elif isinstance(exc, PermissionError):
            kind, retryable = PipelineErrorKind.PERMISSION_DENIED, False
        elif isinstance(exc, ValueError):
            kind, retryable = PipelineErrorKind.INVALID_INPUT, False
        else:
            kind, retryable = PipelineErrorKind.INTERNAL_INVARIANT, False
        return cls(
            kind=kind,
            stage=stage,
            retryable=retryable,
            recoverable=retryable,
            exception_type=type(exc).__name__,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "stage": self.stage,
            "retryable": self.retryable,
            "recoverable": self.recoverable,
            "exception_type": self.exception_type,
        }
