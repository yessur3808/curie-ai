"""Deterministic adverse provider outcomes for offline orchestration evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProviderMode(str, Enum):
    SUCCESS = "success"
    ALREADY_SATISFIED = "already_satisfied"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_EXPIRED = "auth_expired"
    REJECTED = "provider_rejected"
    CONTRADICTORY_READBACK = "contradictory_readback"
    PARTIAL_GROUP = "partial_group_failure"
    RECONNECT_REPLAY = "reconnect_replay"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    mode: ProviderMode
    execution_status: str
    verification_status: str
    mutation_count: int
    final_states: tuple[str, ...]
    error_type: str | None = None
    replay_suppressed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "execution_status": self.execution_status,
            "verification_status": self.verification_status,
            "mutation_count": self.mutation_count,
            "final_states": list(self.final_states),
            "error_type": self.error_type,
            "replay_suppressed": self.replay_suppressed,
        }


def simulate_provider(mode: ProviderMode) -> ProviderObservation:
    """Return the required truthful orchestration outcome for one provider mode."""
    if mode is ProviderMode.SUCCESS:
        return ProviderObservation(mode, "completed", "verified", 1, ("off",))
    if mode is ProviderMode.ALREADY_SATISFIED:
        return ProviderObservation(mode, "completed", "already_satisfied", 0, ("off",))
    if mode is ProviderMode.TIMEOUT:
        return ProviderObservation(
            mode, "failed", "unverified", 1, ("unknown",), "timeout"
        )
    if mode is ProviderMode.RATE_LIMITED:
        return ProviderObservation(
            mode, "failed", "not_attempted", 0, ("on",), "rate_limited"
        )
    if mode is ProviderMode.AUTH_EXPIRED:
        return ProviderObservation(
            mode, "failed", "not_attempted", 0, ("on",), "auth_expired"
        )
    if mode is ProviderMode.REJECTED:
        return ProviderObservation(
            mode, "failed", "rejected", 0, ("on",), "provider_rejected"
        )
    if mode is ProviderMode.CONTRADICTORY_READBACK:
        return ProviderObservation(
            mode, "failed", "contradicted", 1, ("on",), "state_mismatch"
        )
    if mode is ProviderMode.PARTIAL_GROUP:
        return ProviderObservation(
            mode, "partial_failure", "partial", 1, ("off", "on"), "member_failed"
        )
    if mode is ProviderMode.RECONNECT_REPLAY:
        return ProviderObservation(
            mode,
            "completed",
            "verified",
            1,
            ("off",),
            replay_suppressed=True,
        )
    if mode is ProviderMode.CANCELLED:
        return ProviderObservation(
            mode, "cancelled", "not_attempted", 0, ("on",), "cancelled"
        )
    raise ValueError(f"unsupported provider mode: {mode}")


def run_provider_matrix() -> tuple[ProviderObservation, ...]:
    return tuple(simulate_provider(mode) for mode in ProviderMode)


__all__ = [
    "ProviderMode",
    "ProviderObservation",
    "run_provider_matrix",
    "simulate_provider",
]
