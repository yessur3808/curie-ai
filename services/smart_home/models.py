"""Normalized data shared by every smart-home provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return str(value)


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    provider: str
    device_id: str
    name: str
    device_type: str = "device"
    online: bool | None = None
    power: str = "unknown"
    running: bool | None = None
    controllable: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.device_id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "provider": self.provider,
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type,
            "online": self.online,
            "power": self.power,
            "running": self.running,
            "controllable": self.controllable,
            "metrics": _json_value(self.metrics),
            "attributes": _json_value(self.attributes),
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class ProviderIssue:
    provider: str
    message: str
    configured: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "message": self.message,
            "configured": self.configured,
        }


@dataclass(frozen=True, slots=True)
class ControlReceipt:
    provider: str
    device_id: str
    name: str
    requested_state: str
    verified_state: str = "unknown"
    snapshot: DeviceSnapshot | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "device_id": self.device_id,
            "name": self.name,
            "requested_state": self.requested_state,
            "verified_state": self.verified_state,
            "snapshot": self.snapshot.as_dict() if self.snapshot else None,
        }
