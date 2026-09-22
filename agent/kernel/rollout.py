"""Runtime-safe staged rollout and circuit breaking for the turn pipeline.

The rollout state is deliberately tiny, local, and credential-free.  Operators
can change stages or trip the circuit breaker atomically without a redeploy.
Invalid state fails closed to the legacy path while that rollback path exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from utils.redaction import redact_secrets, secret_markers


class RolloutStage(str, Enum):
    OFFLINE = "offline"
    SHADOW = "shadow"
    CONVERSATION_CANARY = "conversation_canary"
    SMART_HOME_CANARY = "smart_home_canary"
    CONNECTOR_EXPANSION = "connector_expansion"
    DEFAULT_ACTIVE = "default_active"
    LEGACY_REMOVED = "legacy_removed"

    @classmethod
    def parse(
        cls, value: object, default: "RolloutStage" | None = None
    ) -> "RolloutStage":
        fallback = default or cls.DEFAULT_ACTIVE
        try:
            return cls(str(value or "").strip().casefold())
        except ValueError:
            return fallback


class RollbackTrigger(str, Enum):
    WRONG_DEVICE_MUTATION = "wrong_device_mutation"
    FALSE_VERIFICATION_CLAIM = "false_verification_claim"
    CROSS_OWNER_DATA = "cross_owner_data"
    TOOL_ACTIVATION_REGRESSION = "tool_activation_regression"
    DELIVERY_FAILURE_REGRESSION = "delivery_failure_regression"
    LATENCY_REGRESSION = "latency_regression"
    UNSUPPORTED_PROACTIVE_CLAIM = "unsupported_proactive_claim"
    INSTABILITY_REGRESSION = "instability_regression"
    SENSITIVE_DATA_LEAK = "sensitive_data_leak"


@dataclass(frozen=True, slots=True)
class LegacyRetention:
    owner: str = "curie-maintainers"
    removal_deadline: str = "2026-10-22"
    rollback_revision: str = "bb258494"

    def as_dict(self) -> dict[str, str]:
        return {
            "owner": self.owner,
            "removal_deadline": self.removal_deadline,
            "rollback_revision": self.rollback_revision,
        }


def rollout_state_path() -> Path:
    configured = os.getenv("CURIE_PIPELINE_ROLLOUT_STATE", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".curie" / "pipeline-rollout.json"
    )


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "stage_override": None,
        "rollback": {"active": False, "trigger": None},
    }


def read_rollout_state(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else rollout_state_path()
    if not target.exists():
        return _default_state()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise ValueError("unsupported rollout state")
        rollback = raw.get("rollback")
        if not isinstance(rollback, Mapping):
            raise ValueError("missing rollback state")
        trigger = rollback.get("trigger")
        if trigger is not None:
            RollbackTrigger(str(trigger))
        override = raw.get("stage_override")
        if override is not None:
            RolloutStage(str(override))
        return {
            "schema_version": 1,
            "stage_override": override,
            "rollback": {
                "active": bool(rollback.get("active")),
                "trigger": trigger,
                "tripped_at": rollback.get("tripped_at"),
                "evidence": redact_secrets(dict(rollback.get("evidence") or {})),
            },
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "schema_version": 1,
            "stage_override": RolloutStage.OFFLINE.value,
            "rollback": {
                "active": True,
                "trigger": "invalid_runtime_state",
                "evidence": {},
            },
        }


def _write_rollout_state(state: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".pipeline-rollout-", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(redact_secrets(dict(state)), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def set_rollout_stage(
    stage: RolloutStage | str, path: str | Path | None = None
) -> dict[str, Any]:
    target = Path(path) if path is not None else rollout_state_path()
    state = read_rollout_state(target)
    selected = RolloutStage(stage)
    state["stage_override"] = selected.value
    _write_rollout_state(state, target)
    return state


def trigger_pipeline_rollback(
    trigger: RollbackTrigger | str,
    *,
    evidence: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    target = Path(path) if path is not None else rollout_state_path()
    selected = RollbackTrigger(trigger)
    state = read_rollout_state(target)
    state["rollback"] = {
        "active": True,
        "trigger": selected.value,
        "tripped_at": datetime.now(timezone.utc).isoformat(),
        "evidence": redact_secrets(dict(evidence or {})),
    }
    _write_rollout_state(state, target)
    return state


def clear_pipeline_rollback(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else rollout_state_path()
    state = read_rollout_state(target)
    state["rollback"] = {"active": False, "trigger": None}
    _write_rollout_state(state, target)
    return state


def effective_rollout_stage(
    configured: RolloutStage, path: str | Path | None = None
) -> RolloutStage:
    state = read_rollout_state(path)
    override = state.get("stage_override")
    return RolloutStage.parse(override, configured) if override else configured


def rollback_active(path: str | Path | None = None) -> bool:
    return bool(read_rollout_state(path)["rollback"]["active"])


def evaluate_rollback_signals(
    signals: Mapping[str, Any],
) -> RollbackTrigger | None:
    """Map privacy-safe runtime measurements to the first blocking trigger."""
    ordered = (
        ("wrong_device_mutation", RollbackTrigger.WRONG_DEVICE_MUTATION, bool),
        ("false_verification_claim", RollbackTrigger.FALSE_VERIFICATION_CLAIM, bool),
        ("cross_owner_data", RollbackTrigger.CROSS_OWNER_DATA, bool),
        (
            "unsupported_proactive_claim",
            RollbackTrigger.UNSUPPORTED_PROACTIVE_CLAIM,
            bool,
        ),
        ("sensitive_data_leak", RollbackTrigger.SENSITIVE_DATA_LEAK, bool),
    )
    for key, trigger, coercion in ordered:
        if coercion(signals.get(key)):
            return trigger
    if float(signals.get("false_tool_activation_rate") or 0) > 0.005:
        return RollbackTrigger.TOOL_ACTIVATION_REGRESSION
    if float(signals.get("delivery_failure_rate_increase") or 0) > 0.05:
        return RollbackTrigger.DELIVERY_FAILURE_REGRESSION
    if int(signals.get("p95_regression_windows") or 0) >= 2:
        return RollbackTrigger.LATENCY_REGRESSION
    if float(signals.get("instability_rate_increase") or 0) > 0.05:
        return RollbackTrigger.INSTABILITY_REGRESSION
    return None


def detect_result_rollback(
    result: Mapping[str, Any], *, expected_owner: str = ""
) -> RollbackTrigger | None:
    verification = str(result.get("verification_status") or "").casefold()
    observed_owner = str(result.get("owner_id") or result.get("internal_id") or "")
    signals = {
        "wrong_device_mutation": bool(result.get("wrong_device_mutation")),
        "false_verification_claim": bool(result.get("reported_verified"))
        and verification not in {"verified", "already_satisfied"},
        "cross_owner_data": bool(
            expected_owner and observed_owner and expected_owner != observed_owner
        ),
        "unsupported_proactive_claim": bool(result.get("unsupported_proactive_claim")),
        "sensitive_data_leak": bool(secret_markers(str(result.get("text") or ""))),
    }
    return evaluate_rollback_signals(signals)


def rollout_status(
    configured: RolloutStage = RolloutStage.DEFAULT_ACTIVE,
    *,
    path: str | Path | None = None,
    retention: LegacyRetention | None = None,
) -> dict[str, Any]:
    state = read_rollout_state(path)
    return {
        "configured_stage": configured.value,
        "effective_stage": effective_rollout_stage(configured, path).value,
        "rollback": state["rollback"],
        "legacy_retention": (retention or LegacyRetention()).as_dict(),
    }


__all__ = [
    "LegacyRetention",
    "RollbackTrigger",
    "RolloutStage",
    "clear_pipeline_rollback",
    "detect_result_rollback",
    "effective_rollout_stage",
    "evaluate_rollback_signals",
    "read_rollout_state",
    "rollback_active",
    "rollout_state_path",
    "rollout_status",
    "set_rollout_stage",
    "trigger_pipeline_rollback",
]
