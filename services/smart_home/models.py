"""Normalized data shared by every smart-home provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


_SENSITIVE_KEY = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "cookie",
)


def _safe_metadata(values: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        name = str(key)
        if any(fragment in name.casefold() for fragment in _SENSITIVE_KEY):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            safe[name] = value
    return safe


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
class CanonicalDevice:
    """Provider-neutral inventory identity with no credential-bearing fields."""

    canonical_id: str
    provider: str
    provider_id: str
    display_name: str
    normalized_name: str
    device_type: str
    room: str | None
    capabilities: frozenset[str]
    state_schema: Mapping[str, str]
    online_status: bool | None
    last_observed_at: str
    confirmed_aliases: tuple[str, ...] = ()
    rejected_aliases: tuple[str, ...] = ()
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    verification_supported: bool = False
    power: str = "unknown"
    running: bool | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)
    controllable: bool = False
    inventory_available: bool = True

    def __post_init__(self) -> None:
        if not self.canonical_id or not self.provider or not self.provider_id:
            raise ValueError("Canonical devices require provider-scoped identity")
        if not self.display_name.strip() or not self.normalized_name.strip():
            raise ValueError("Canonical devices require a display name")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: DeviceSnapshot,
        *,
        normalized_name: str,
        confirmed_aliases: tuple[str, ...] = (),
        rejected_aliases: tuple[str, ...] = (),
    ) -> "CanonicalDevice":
        attributes = _safe_metadata(snapshot.attributes)
        identity = " ".join(
            (
                normalized_name,
                str(snapshot.device_type).casefold(),
                str(attributes.get("device_class") or "").casefold(),
                str(attributes.get("category") or "").casefold(),
                str(attributes.get("model") or "").casefold(),
            )
        )
        capabilities: set[str] = set()
        if snapshot.controllable:
            capabilities.update(("power", "controllable"))
        if any(
            token in identity.split()
            for token in ("light", "lights", "lamp", "lamps", "bulb", "bulbs", "led")
        ) or any(
            phrase in identity for phrase in ("sync box", "backlight", "nanoleaf")
        ):
            capabilities.update(("light", "illumination"))
        if "switch" in identity.split() or snapshot.device_type.casefold() == "switch":
            capabilities.add("switch")
        if "brightness_percent" in snapshot.metrics:
            capabilities.update(("light", "illumination", "brightness"))
        room = next(
            (
                str(attributes[key]).strip()
                for key in ("room", "area", "location", "room_name")
                if attributes.get(key)
            ),
            None,
        )
        state_schema = {
            "online": "boolean|null",
            "power": "on|off|unknown",
            "running": "boolean|null",
            **{
                str(key): type(value).__name__
                for key, value in snapshot.metrics.items()
            },
        }
        return cls(
            canonical_id=snapshot.key,
            provider=snapshot.provider,
            provider_id=snapshot.device_id,
            display_name=snapshot.name,
            normalized_name=normalized_name,
            device_type=snapshot.device_type,
            room=room,
            capabilities=frozenset(capabilities),
            state_schema=state_schema,
            online_status=snapshot.online,
            last_observed_at=snapshot.observed_at,
            confirmed_aliases=confirmed_aliases,
            rejected_aliases=rejected_aliases,
            provider_metadata=attributes,
            verification_supported=snapshot.controllable,
            power=snapshot.power,
            running=snapshot.running,
            metrics=dict(snapshot.metrics),
            controllable=snapshot.controllable,
        )

    def to_snapshot(self) -> DeviceSnapshot:
        attributes = dict(self.provider_metadata)
        if self.room:
            attributes.setdefault("room", self.room)
        if not self.inventory_available:
            attributes["inventory_status"] = "unavailable"
        return DeviceSnapshot(
            self.provider,
            self.provider_id,
            self.display_name,
            self.device_type,
            self.online_status if self.inventory_available else False,
            self.power,
            self.running,
            self.controllable,
            dict(self.metrics),
            attributes,
            self.last_observed_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "provider": self.provider,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "normalized_name": self.normalized_name,
            "device_type": self.device_type,
            "room": self.room,
            "capabilities": sorted(self.capabilities),
            "state_schema": dict(self.state_schema),
            "online_status": self.online_status,
            "last_observed_at": self.last_observed_at,
            "confirmed_aliases": list(self.confirmed_aliases),
            "rejected_aliases": list(self.rejected_aliases),
            "provider_metadata": _safe_metadata(self.provider_metadata),
            "verification_supported": self.verification_supported,
            "power": self.power,
            "running": self.running,
            "metrics": _json_value(self.metrics),
            "controllable": self.controllable,
            "inventory_available": self.inventory_available,
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
