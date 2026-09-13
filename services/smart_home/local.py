"""Local-network providers and the Petlibro Home Assistant bridge."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import normalize_power, require_power_state, simple_metrics
from .config import json_list, setting
from .models import ControlReceipt, DeviceSnapshot


def _lan_host(raw: str) -> str:
    host = str(raw).strip()
    if not host or "/" in host or "@" in host or "://" in host:
        raise ValueError("Local device host must be a plain LAN hostname or IP address")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", host):
            raise ValueError("Local device host contains invalid characters")
        if "." in host and not host.casefold().endswith(".local"):
            raise ValueError(
                "Local device hostnames must be single-label or end in .local"
            )
    else:
        if not (address.is_private or address.is_link_local or address.is_loopback):
            raise ValueError("Local device host must resolve to a private LAN address")
    return host


def _local_base_url(raw: str, default_port: int | None = None) -> str:
    value = str(raw).strip()
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "Local bridge URL must use HTTP(S) without embedded credentials"
        )
    _lan_host(parsed.hostname)
    port = parsed.port or default_port
    authority = f"{parsed.hostname}:{port}" if port else parsed.hostname
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{authority}{path}"


class NanoleafProvider:
    name = "nanoleaf"

    def _devices(self, owner_id: str) -> list[dict[str, Any]]:
        devices = json_list("NANOLEAF_DEVICES_JSON")
        vault_devices = setting(
            owner_id, self.name, "devices", "CURIE_UNUSED_NANOLEAF_DEVICES", None
        )
        return (
            [dict(item) for item in vault_devices]
            if isinstance(vault_devices, list)
            else devices
        )

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        if self._devices(owner_id):
            return True, None
        return (
            False,
            "NANOLEAF_DEVICES_JSON (or an encrypted devices list) is not configured",
        )

    @staticmethod
    def _identity(config: Mapping[str, Any]) -> tuple[str, str, str]:
        host = _lan_host(str(config.get("host", "")))
        token = str(config.get("token", "")).strip()
        if not token:
            raise ValueError(f"Nanoleaf token is missing for {host}")
        return str(config.get("id") or host), host, token

    @staticmethod
    def _snapshot(
        config: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> DeviceSnapshot:
        device_id, _host, _token = NanoleafProvider._identity(config)
        on = (
            payload.get("state", {}).get("on", {}).get("value")
            if isinstance(payload.get("state"), Mapping)
            else None
        )
        power = normalize_power(on)
        metrics = simple_metrics(payload)
        state = (
            payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
        )
        for key in ("brightness", "hue", "sat", "ct", "colorMode"):
            value = state.get(key)
            if isinstance(value, Mapping):
                value = value.get("value")
            if value is not None:
                metrics[key] = value
        return DeviceSnapshot(
            provider="nanoleaf",
            device_id=device_id,
            name=str(config.get("name") or payload.get("name") or device_id),
            device_type=str(payload.get("model") or "light panels"),
            online=True,
            power=power,
            running=power == "on" if power != "unknown" else None,
            controllable=on is not None,
            metrics=metrics,
            attributes={"manufacturer": payload.get("manufacturer")},
        )

    async def _get(
        self, client: httpx.AsyncClient, config: Mapping[str, Any]
    ) -> DeviceSnapshot:
        _device_id, host, token = self._identity(config)
        response = await client.get(f"http://{host}:16021/api/v1/{token}")
        response.raise_for_status()
        return self._snapshot(config, response.json())

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        devices = self._devices(owner_id)
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            results = await asyncio.gather(
                *(self._get(client, item) for item in devices), return_exceptions=True
            )
        snapshots = []
        for config, result in zip(devices, results):
            if isinstance(result, Exception):
                device_id, _host, _token = self._identity(config)
                snapshots.append(
                    DeviceSnapshot(
                        "nanoleaf",
                        device_id,
                        str(config.get("name") or device_id),
                        "light panels",
                        False,
                        controllable=True,
                    )
                )
            else:
                snapshots.append(result)
        return snapshots

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        matches = [
            item
            for item in self._devices(owner_id)
            if self._identity(item)[0] == device_id
        ]
        if not matches:
            raise LookupError("That Nanoleaf device is no longer configured")
        config = matches[0]
        _identity, host, token = self._identity(config)
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.put(
                f"http://{host}:16021/api/v1/{token}/state/on",
                json={"on": {"value": state == "on"}},
            )
            response.raise_for_status()
            snapshot = await self._get(client, config)
        return ControlReceipt(
            self.name, device_id, snapshot.name, state, snapshot.power, snapshot
        )


class TapoProvider:
    name = "tapo"

    def _devices(self, owner_id: str) -> list[dict[str, Any]]:
        devices = json_list("TAPO_DEVICES_JSON")
        vault_devices = setting(
            owner_id, self.name, "devices", "CURIE_UNUSED_TAPO_DEVICES", None
        )
        return (
            [dict(item) for item in vault_devices]
            if isinstance(vault_devices, list)
            else devices
        )

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        if not self._devices(owner_id):
            return (
                False,
                "TAPO_DEVICES_JSON (or an encrypted devices list) is not configured",
            )
        try:
            import kasa  # noqa: F401
        except ImportError:
            return False, "Install the optional python-kasa dependency"
        return True, None

    async def _connect(self, owner_id: str, config: Mapping[str, Any]):
        from kasa import Discover

        host = _lan_host(str(config.get("host", "")))
        username = str(
            config.get("username")
            or setting(owner_id, self.name, "username", "TAPO_USERNAME", "")
        )
        password = str(
            config.get("password")
            or setting(owner_id, self.name, "password", "TAPO_PASSWORD", "")
        )
        kwargs: dict[str, Any] = {}
        if username or password:
            try:
                from kasa import Credentials

                kwargs["credentials"] = Credentials(username, password)
            except ImportError:
                kwargs.update(username=username, password=password)
        device = await Discover.discover_single(host, **kwargs)
        if device is None:
            raise ConnectionError(f"No Tapo device responded at {host}")
        await device.update()
        return device

    @staticmethod
    def _snapshot(config: Mapping[str, Any], device: Any) -> DeviceSnapshot:
        host = _lan_host(str(config.get("host", "")))
        on = getattr(device, "is_on", None)
        metrics: dict[str, Any] = {}
        for source, target in (
            ("current_consumption", "power_w"),
            ("emeter_today", "energy_today_kwh"),
            ("emeter_this_month", "energy_month_kwh"),
        ):
            value = getattr(device, source, None)
            if value is not None and isinstance(value, (str, int, float, bool)):
                metrics[target] = value
        return DeviceSnapshot(
            provider="tapo",
            device_id=str(config.get("id") or host),
            name=str(config.get("name") or getattr(device, "alias", None) or host),
            device_type=str(
                getattr(device, "device_type", None)
                or getattr(device, "model", None)
                or "device"
            ),
            online=True,
            power=normalize_power(on),
            running=bool(on) if isinstance(on, bool) else None,
            controllable=callable(getattr(device, "turn_on", None))
            and callable(getattr(device, "turn_off", None)),
            metrics=metrics,
            attributes={"model": getattr(device, "model", None)},
        )

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        devices = self._devices(owner_id)
        results = await asyncio.gather(
            *(self._connect(owner_id, item) for item in devices), return_exceptions=True
        )
        snapshots = []
        for config, result in zip(devices, results):
            host = _lan_host(str(config.get("host", "")))
            if isinstance(result, Exception):
                snapshots.append(
                    DeviceSnapshot(
                        "tapo",
                        str(config.get("id") or host),
                        str(config.get("name") or host),
                        online=False,
                        controllable=True,
                    )
                )
            else:
                snapshots.append(self._snapshot(config, result))
        return snapshots

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        matches = [
            item
            for item in self._devices(owner_id)
            if str(item.get("id") or item.get("host")) == device_id
        ]
        if not matches:
            raise LookupError("That Tapo device is no longer configured")
        config = matches[0]
        device = await self._connect(owner_id, config)
        await (device.turn_on() if state == "on" else device.turn_off())
        await device.update()
        snapshot = self._snapshot(config, device)
        return ControlReceipt(
            self.name, device_id, snapshot.name, state, snapshot.power, snapshot
        )


class MiHomeProvider:
    name = "mi_home"

    def _devices(self, owner_id: str) -> list[dict[str, Any]]:
        devices = json_list("MI_HOME_DEVICES_JSON")
        vault_devices = setting(
            owner_id, self.name, "devices", "CURIE_UNUSED_MI_HOME_DEVICES", None
        )
        return (
            [dict(item) for item in vault_devices]
            if isinstance(vault_devices, list)
            else devices
        )

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        if not self._devices(owner_id):
            return (
                False,
                "MI_HOME_DEVICES_JSON (or an encrypted devices list) is not configured",
            )
        try:
            import miio  # noqa: F401
        except ImportError:
            return False, "Install the optional python-miio dependency"
        return True, None

    @staticmethod
    def _client(config: Mapping[str, Any]):
        from miio import Device

        host = _lan_host(str(config.get("host", "")))
        token = str(config.get("token", "")).strip()
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", token):
            raise ValueError(
                f"Mi Home token for {host} must be 32 hexadecimal characters"
            )
        return Device(host, token)

    @staticmethod
    def _read_sync(config: Mapping[str, Any]) -> DeviceSnapshot:
        device = MiHomeProvider._client(config)
        host = _lan_host(str(config.get("host", "")))
        properties = list(
            config.get("properties") or ["power"]
        )
        values = device.send(str(config.get("status_method") or "get_prop"), properties)
        values = values if isinstance(values, list) else [values]
        status = dict(zip(properties, values))
        power = normalize_power(
            status.get(str(config.get("power_property") or "power"))
        )
        metrics = simple_metrics(status)
        for key, value in status.items():
            if key != str(config.get("power_property") or "power") and isinstance(
                value, (str, int, float, bool)
            ):
                metrics.setdefault(str(key), value)
        return DeviceSnapshot(
            provider="mi_home",
            device_id=str(config.get("id") or host),
            name=str(config.get("name") or host),
            device_type=str(config.get("model") or "miio device"),
            online=True,
            power=power,
            running=power == "on" if power != "unknown" else None,
            controllable=bool(config.get("set_power_method", "set_power")),
            metrics=metrics,
        )

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        devices = self._devices(owner_id)
        results = await asyncio.gather(
            *(asyncio.to_thread(self._read_sync, item) for item in devices),
            return_exceptions=True,
        )
        snapshots = []
        for config, result in zip(devices, results):
            host = _lan_host(str(config.get("host", "")))
            if isinstance(result, Exception):
                snapshots.append(
                    DeviceSnapshot(
                        "mi_home",
                        str(config.get("id") or host),
                        str(config.get("name") or host),
                        str(config.get("model") or "miio device"),
                        False,
                        controllable=True,
                    )
                )
            else:
                snapshots.append(result)
        return snapshots

    @staticmethod
    def _set_sync(config: Mapping[str, Any], state: str) -> DeviceSnapshot:
        device = MiHomeProvider._client(config)
        method = str(config.get("set_power_method") or "set_power")
        params = config.get("on_params" if state == "on" else "off_params", [state])
        params = params if isinstance(params, list) else [params]
        callable_method = getattr(device, method, None)
        if callable(callable_method):
            callable_method(*params)
        else:
            device.send(method, params)
        return MiHomeProvider._read_sync(config)

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        matches = [
            item
            for item in self._devices(owner_id)
            if str(item.get("id") or item.get("host")) == device_id
        ]
        if not matches:
            raise LookupError("That Mi Home device is no longer configured")
        snapshot = await asyncio.to_thread(self._set_sync, matches[0], state)
        return ControlReceipt(
            self.name, device_id, snapshot.name, state, snapshot.power, snapshot
        )


class PetlibroProvider:
    """Petlibro devices exposed through Home Assistant's stable REST API."""

    name = "petlibro"

    def _url(self, owner_id: str) -> str:
        raw = str(setting(owner_id, self.name, "ha_url", "PETLIBRO_HA_URL", ""))
        return _local_base_url(raw) if raw else ""

    def _token(self, owner_id: str) -> str:
        return str(setting(owner_id, self.name, "ha_token", "PETLIBRO_HA_TOKEN", ""))

    def _entities(self, owner_id: str) -> list[dict[str, Any]]:
        entities = json_list("PETLIBRO_ENTITIES_JSON")
        vault_entities = setting(
            owner_id, self.name, "entities", "CURIE_UNUSED_PETLIBRO_ENTITIES", None
        )
        return (
            [dict(item) for item in vault_entities]
            if isinstance(vault_entities, list)
            else entities
        )

    def configured(self, owner_id: str) -> tuple[bool, str | None]:
        if self._url(owner_id) and self._token(owner_id):
            return True, None
        return (
            False,
            "PETLIBRO_HA_URL and PETLIBRO_HA_TOKEN (or encrypted equivalents) are not configured",
        )

    def _client(self, owner_id: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._url(owner_id),
            headers={"Authorization": f"Bearer {self._token(owner_id)}"},
            timeout=12,
            trust_env=False,
        )

    @staticmethod
    def _snapshot(
        row: Mapping[str, Any], config: Mapping[str, Any] | None = None
    ) -> DeviceSnapshot:
        config = config or {}
        entity_id = str(row.get("entity_id") or config.get("entity_id") or "")
        state = str(row.get("state") or "unknown")
        attributes = (
            row.get("attributes") if isinstance(row.get("attributes"), Mapping) else {}
        )
        power = normalize_power(state)
        domain = entity_id.partition(".")[0]
        running = state.casefold() in {"on", "running", "feeding", "dispensing"}
        return DeviceSnapshot(
            provider="petlibro",
            device_id=entity_id,
            name=str(
                config.get("name") or attributes.get("friendly_name") or entity_id
            ),
            device_type=str(
                config.get("type")
                or attributes.get("device_class")
                or domain
                or "device"
            ),
            online=state.casefold() not in {"unavailable", "unknown"},
            power=power,
            running=(
                running
                if power != "unknown"
                or state.casefold() in {"running", "feeding", "dispensing"}
                else None
            ),
            controllable=domain in {"switch", "input_boolean", "light", "fan"},
            metrics=simple_metrics(attributes),
            attributes={
                key: value
                for key, value in attributes.items()
                if key in {"model", "device_class", "unit_of_measurement"}
            },
        )

    async def list_devices(self, owner_id: str) -> list[DeviceSnapshot]:
        configured = self._entities(owner_id)
        async with self._client(owner_id) as client:
            if configured:
                responses = await asyncio.gather(
                    *(
                        client.get(f"/api/states/{item['entity_id']}")
                        for item in configured
                    ),
                    return_exceptions=True,
                )
                snapshots = []
                for config, response in zip(configured, responses):
                    if isinstance(response, Exception) or response.status_code >= 400:
                        snapshots.append(
                            DeviceSnapshot(
                                "petlibro",
                                str(config.get("entity_id")),
                                str(config.get("name") or config.get("entity_id")),
                                online=False,
                            )
                        )
                    else:
                        snapshots.append(self._snapshot(response.json(), config))
                return snapshots
            response = await client.get("/api/states")
            response.raise_for_status()
            rows = response.json()
        return [
            self._snapshot(row)
            for row in rows
            if isinstance(row, Mapping)
            and (
                "petlibro" in str(row.get("entity_id", "")).casefold()
                or "petlibro" in str(row.get("attributes", {})).casefold()
            )
        ]

    async def set_power(
        self, owner_id: str, device_id: str, state: str
    ) -> ControlReceipt:
        state = require_power_state(state)
        domain = device_id.partition(".")[0]
        if domain not in {"switch", "input_boolean", "light", "fan"}:
            raise ValueError("That Petlibro entity does not expose on/off control")
        async with self._client(owner_id) as client:
            response = await client.post(
                f"/api/services/homeassistant/turn_{state}",
                json={"entity_id": device_id},
            )
            response.raise_for_status()
            status = await client.get(f"/api/states/{device_id}")
            status.raise_for_status()
        snapshot = self._snapshot(status.json())
        return ControlReceipt(
            self.name, device_id, snapshot.name, state, snapshot.power, snapshot
        )
