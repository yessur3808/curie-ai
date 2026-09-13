"""Provider contract and common normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .models import ControlReceipt, DeviceSnapshot


class SmartHomeProvider(Protocol):
    name: str

    def configured(self, owner_id: str) -> tuple[bool, str | None]: ...

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]: ...

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt: ...


def nested_values(value: Any, key: str) -> list[Any]:
    """Find values for a key in a vendor response without retaining raw payloads."""
    found: list[Any] = []
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            if str(child_key).casefold() == key.casefold():
                if isinstance(child, Mapping) and "value" in child:
                    found.append(child["value"])
                else:
                    found.append(child)
            found.extend(nested_values(child, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.extend(nested_values(child, key))
    return found


def first_nested(value: Any, *keys: str) -> Any:
    for key in keys:
        candidates = nested_values(value, key)
        if candidates:
            for candidate in candidates:
                current = candidate
                while isinstance(current, Mapping):
                    if "value" in current:
                        current = current["value"]
                    elif len(current) == 1:
                        current = next(iter(current.values()))
                    else:
                        break
                if not isinstance(current, (Mapping, list, tuple, set)):
                    return current
            return candidates[0]
    return None


def normalize_power(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        return "on" if value else "off"
    text = str(value or "").strip().casefold()
    if text in {"on", "power_on", "powered_on", "active", "running", "1", "true"}:
        return "on"
    if text in {
        "off",
        "power_off",
        "powered_off",
        "inactive",
        "standby",
        "0",
        "false",
    }:
        return "off"
    return "unknown"


def simple_metrics(payload: Any) -> dict[str, Any]:
    aliases = {
        "battery": ("battery", "batteryLevel", "batteryPercentage"),
        "power_w": ("power", "powerMeter", "currentPower", "powerConsumption"),
        "energy_kwh": ("energy", "energyMeter", "energyUsage"),
        "temperature_c": ("temperature", "currentTemperature", "currentTemperatureC"),
        "humidity_percent": ("humidity", "humidityMeasurement"),
        "brightness_percent": ("brightness",),
        "air_quality": ("airQuality", "pm25", "pm2.5"),
        "filter_percent": ("filterLife", "filterRemaining"),
        "water_percent": ("waterLevel",),
        "food_percent": ("foodLevel",),
    }
    metrics: dict[str, Any] = {}
    for normalized, keys in aliases.items():
        item = first_nested(payload, *keys)
        if item is not None and isinstance(item, (str, int, float, bool)):
            metrics[normalized] = item
    return metrics


def require_power_state(state: str) -> str:
    normalized = str(state).strip().casefold()
    if normalized not in {"on", "off"}:
        raise ValueError("Power state must be 'on' or 'off'")
    return normalized
