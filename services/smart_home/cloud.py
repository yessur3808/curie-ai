"""Cloud API providers with public consumer APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx

from .base import first_nested, normalize_power, require_power_state, simple_metrics
from .config import setting
from .models import ControlReceipt, DeviceSnapshot


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("items", "devices", "data"):
        child = payload.get(key)
        if isinstance(child, list):
            return [dict(item) for item in child if isinstance(item, Mapping)]
        if isinstance(child, Mapping):
            nested = _items(child)
            if nested:
                return nested
    return []


class SmartThingsProvider:
    name = "smartthings"
    base_url = "https://api.smartthings.com/v1"

    def _token(self, owner_id: str) -> str:
        return str(setting(owner_id, self.name, "token", "SMARTTHINGS_TOKEN", ""))

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        if self._token(owner_id):
            return True, None
        return (
            False,
            "SMARTTHINGS_TOKEN (or an encrypted smart_home:smartthings credential) is not configured",
        )

    def _client(self, owner_id: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self._token(owner_id)}"},
            timeout=15,
        )

    @staticmethod
    def _snapshot(device: Mapping[str, Any], status: Any = None) -> DeviceSnapshot:
        status = status if status is not None else device.get("status", {})
        health = device.get("health", {})
        switch = first_nested(status, "switch")
        power = normalize_power(switch)
        online_value = first_nested(health, "state", "status")
        online_text = str(online_value or "").casefold()
        online = (
            True
            if online_text in {"online", "connected"}
            else False if online_text in {"offline", "disconnected"} else None
        )
        device_id = str(device.get("deviceId") or device.get("id") or "")
        activity = first_nested(status, "machineState", "operatingState", "activity")
        activity_text = str(activity or "").casefold()
        running = (
            True
            if activity_text in {"run", "running", "active", "working"}
            else False
            if activity_text in {"idle", "paused", "stopped", "inactive"}
            else power == "on"
            if power != "unknown"
            else None
        )
        return DeviceSnapshot(
            provider="smartthings",
            device_id=device_id,
            name=str(device.get("label") or device.get("name") or device_id),
            device_type=str(
                device.get("type") or device.get("deviceTypeName") or "device"
            ),
            online=online,
            power=power,
            running=running,
            controllable=switch is not None,
            metrics=simple_metrics(status),
            attributes={
                "room": first_nested(device, "roomName"),
                "manufacturer": device.get("manufacturerName"),
            },
        )

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        async with self._client(owner_id) as client:
            response = await client.get(
                "/devices", params={"includeStatus": "true", "includeHealth": "true"}
            )
            response.raise_for_status()
            devices = _items(response.json())
        return [self._snapshot(device) for device in devices]

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        async with self._client(owner_id) as client:
            response = await client.post(
                f"/devices/{device_id}/commands",
                json={
                    "commands": [
                        {
                            "component": "main",
                            "capability": "switch",
                            "command": state,
                            "arguments": [],
                        }
                    ]
                },
            )
            response.raise_for_status()
            status_response = await client.get(f"/devices/{device_id}/status")
            status_response.raise_for_status()
            detail_response = await client.get(f"/devices/{device_id}")
            detail_response.raise_for_status()
        device = detail_response.json()
        snapshot = self._snapshot(device, status_response.json())
        return ControlReceipt(
            self.name, device_id, snapshot.name, state, snapshot.power, snapshot
        )


class GoveeProvider:
    name = "govee"
    base_url = "https://openapi.api.govee.com"

    def _key(self, owner_id: str) -> str:
        return str(setting(owner_id, self.name, "api_key", "GOVEE_API_KEY", ""))

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        if self._key(owner_id):
            return True, None
        return (
            False,
            "GOVEE_API_KEY (or an encrypted smart_home:govee credential) is not configured",
        )

    def _client(self, owner_id: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Govee-API-Key": self._key(owner_id),
                "Content-Type": "application/json",
            },
            timeout=15,
        )

    @staticmethod
    def _capability_value(payload: Any, instance: str) -> Any:
        if isinstance(payload, Mapping):
            if str(payload.get("instance", "")).casefold() == instance.casefold():
                value = payload.get("state", payload.get("value"))
                if isinstance(value, Mapping) and "value" in value:
                    value = value["value"]
                return value
            for value in payload.values():
                found = GoveeProvider._capability_value(value, instance)
                if found is not None:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = GoveeProvider._capability_value(value, instance)
                if found is not None:
                    return found
        return None

    @classmethod
    def _snapshot(cls, device: Mapping[str, Any], state_payload: Any) -> DeviceSnapshot:
        power_value = cls._capability_value(state_payload, "powerSwitch")
        power = normalize_power(power_value)
        online_value = cls._capability_value(state_payload, "online")
        online = bool(online_value) if online_value is not None else None
        device_id = str(device.get("device") or device.get("deviceId") or "")
        return DeviceSnapshot(
            provider="govee",
            device_id=device_id,
            name=str(device.get("deviceName") or device.get("name") or device_id),
            device_type=str(device.get("type") or device.get("sku") or "device"),
            online=online,
            power=power,
            running=power == "on" if power != "unknown" else None,
            controllable=power_value is not None,
            metrics=simple_metrics(state_payload),
            attributes={"sku": device.get("sku")},
        )

    async def _state(self, client: httpx.AsyncClient, device: Mapping[str, Any]) -> Any:
        response = await client.post(
            "/router/api/v1/device/state",
            json={
                "requestId": str(uuid4()),
                "payload": {
                    "sku": device.get("sku"),
                    "device": device.get("device") or device.get("deviceId"),
                },
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        async with self._client(owner_id) as client:
            response = await client.get("/router/api/v1/user/devices")
            response.raise_for_status()
            devices = _items(response.json())
            states = await asyncio.gather(
                *(self._state(client, item) for item in devices), return_exceptions=True
            )
        snapshots = []
        for device, state in zip(devices, states):
            if isinstance(state, Exception):
                state = {}
            snapshots.append(self._snapshot(device, state))
        return snapshots

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        async with self._client(owner_id) as client:
            response = await client.get("/router/api/v1/user/devices")
            response.raise_for_status()
            matches = [
                item
                for item in _items(response.json())
                if str(item.get("device") or item.get("deviceId")) == device_id
            ]
            if not matches:
                raise LookupError("That Govee device is no longer available")
            device = matches[0]
            response = await client.post(
                "/router/api/v1/device/control",
                json={
                    "requestId": str(uuid4()),
                    "payload": {
                        "sku": device.get("sku"),
                        "device": device_id,
                        "capability": {
                            "type": "devices.capabilities.on_off",
                            "instance": "powerSwitch",
                            "value": 1 if state == "on" else 0,
                        },
                    },
                },
            )
            response.raise_for_status()
            state_payload = await self._state(client, device)
            snapshot = self._snapshot(device, state_payload)
            # Govee state reads can briefly lag a successful control response.
            # Retry verification so Curie does not report a stale state as final.
            for delay in (0.5, 1.0):
                if snapshot.power == state:
                    break
                await asyncio.sleep(delay)
                state_payload = await self._state(client, device)
                snapshot = self._snapshot(device, state_payload)
        return ControlReceipt(
            self.name, device_id, snapshot.name, state, snapshot.power, snapshot
        )
