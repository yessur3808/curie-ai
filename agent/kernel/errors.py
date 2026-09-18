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
        if isinstance(exc, TimeoutError):
            kind, retryable = PipelineErrorKind.TIMEOUT, True
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
