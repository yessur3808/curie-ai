"""LG ThinQ Connect provider for air conditioners and air-care devices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import first_nested, normalize_power, require_power_state, simple_metrics
from .config import setting
from .models import ControlReceipt, DeviceSnapshot


def _device_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("devices", "items", "data"):
            rows = _device_rows(payload.get(key))
            if rows:
                return rows
    return []


class LGThinQProvider:
    name = "lg_thinq"

    def _config(self, owner_id: str) -> tuple[str, str, str]:
        token = str(setting(owner_id, self.name, "pat", "LG_THINQ_PAT", ""))
        country = str(setting(owner_id, self.name, "country", "LG_THINQ_COUNTRY", "US"))
        client_id = str(
            setting(owner_id, self.name, "client_id", "LG_THINQ_CLIENT_ID", "")
        )
        return token, country, client_id

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        token, _country, client_id = self._config(owner_id)
        if not token or not client_id:
            return (
                False,
                "LG_THINQ_PAT and LG_THINQ_CLIENT_ID (or encrypted equivalents) are not configured",
            )
        try:
            import aiohttp  # noqa: F401
            import thinqconnect  # noqa: F401
        except ImportError:
            return False, "Install the optional aiohttp and thinqconnect dependencies"
        return True, None

    @staticmethod
    def _is_air_device(device: Mapping[str, Any]) -> bool:
        device_type = str(
            device.get("deviceType") or device.get("type") or ""
        ).casefold()
        return any(
            word in device_type
            for word in (
                "air_conditioner",
                "air conditioner",
                "air_purifier",
                "air purifier",
                "dehumidifier",
                "humidifier",
            )
        )

    @staticmethod
    def _snapshot(
        device: Mapping[str, Any], status: Mapping[str, Any]
    ) -> DeviceSnapshot:
        device_id = str(device.get("deviceId") or device.get("id") or "")
        mode = first_nested(
            status,
            "airConOperationMode",
            "airCleanOperationMode",
            "operationMode",
            "power",
        )
        power = normalize_power(mode)
        online_value = device.get("reportable")
        if online_value is None:
            online_value = first_nested(status, "online", "connected")
        online = bool(online_value) if online_value is not None else None
        return DeviceSnapshot(
            provider="lg_thinq",
            device_id=device_id,
            name=str(
                device.get("alias")
                or device.get("deviceName")
                or device.get("name")
                or device_id
            ),
            device_type=str(
                device.get("deviceType") or device.get("type") or "LG air device"
            ),
            online=online,
            power=power,
            running=power == "on" if power != "unknown" else None,
            controllable=first_nested(status, "airConOperationMode") is not None,
            metrics=simple_metrics(status),
            attributes={
                "model": device.get("modelName"),
                "room": device.get("roomName"),
            },
        )

    async def _api(self, owner_id: str):
        from aiohttp import ClientSession
        from thinqconnect import ThinQApi

        token, country, client_id = self._config(owner_id)
        session = ClientSession()
        return (
            ThinQApi(
                session=session,
                access_token=token,
                country_code=country,
                client_id=client_id,
            ),
            session,
        )

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        api, session = await self._api(owner_id)
        try:
            devices = [
                item
                for item in _device_rows(await api.async_get_device_list())
                if self._is_air_device(item)
            ]
            snapshots = []
            for device in devices:
                status = (
                    await api.async_get_device_status(
                        str(device.get("deviceId") or device.get("id"))
                    )
                    or {}
                )
                snapshots.append(self._snapshot(device, status))
            return snapshots
        finally:
            await session.close()

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        api, session = await self._api(owner_id)
        try:
            devices = [
                item
                for item in _device_rows(await api.async_get_device_list())
                if str(item.get("deviceId") or item.get("id")) == device_id
            ]
            if not devices:
                raise LookupError("That LG ThinQ air device is no longer available")
            device = devices[0]
            profile = await api.async_get_device_profile(device_id) or {}
            operation = (
                profile.get("operation") if isinstance(profile, Mapping) else None
            )
            mode_profile = (
                operation.get("airConOperationMode")
                if isinstance(operation, Mapping)
                else None
            )
            if isinstance(mode_profile, Mapping) and "w" not in mode_profile.get(
                "mode", ["w"]
            ):
                raise ValueError("This LG device reports its power mode as read-only")
            await api.async_post_device_control(
                device_id,
                {
                    "operation": {
                        "airConOperationMode": (
                            "POWER_ON" if state == "on" else "POWER_OFF"
                        )
                    }
                },
            )
            status = await api.async_get_device_status(device_id) or {}
            snapshot = self._snapshot(device, status)
            return ControlReceipt(
                self.name, device_id, snapshot.name, state, snapshot.power, snapshot
            )
        finally:
            await session.close()
