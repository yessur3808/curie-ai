"""Aggregate discovery, analysis, target resolution, and control receipts."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from typing import Any

from .base import SmartHomeProvider, require_power_state
from .cloud import GoveeProvider, SmartThingsProvider
from .lg import LGThinQProvider
from .local import MiHomeProvider, NanoleafProvider, PetlibroProvider, TapoProvider
from .models import ControlReceipt, DeviceSnapshot, ProviderIssue


PROVIDER_ALIASES = {
    "smartthings": "smartthings",
    "smart things": "smartthings",
    "govee": "govee",
    "nanoleaf": "nanoleaf",
    "nano leaf": "nanoleaf",
    "tapo": "tapo",
    "tp link": "tapo",
    "tp-link": "tapo",
    "mi home": "mi_home",
    "mihome": "mi_home",
    "xiaomi": "mi_home",
    "lg": "lg_thinq",
    "lg air": "lg_thinq",
    "lg thinq": "lg_thinq",
    "thinq": "lg_thinq",
    "petlibro": "petlibro",
}


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _provider_name(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _normalize(value)
    return PROVIDER_ALIASES.get(normalized, normalized.replace(" ", "_"))


def _semantic_aliases(device: DeviceSnapshot) -> str:
    """Add conservative product-family aliases missing from provider names."""
    name = _normalize(device.name)
    aliases: list[str] = []
    if "dreamview" in name or "sync box" in name:
        aliases.extend(("tv", "television", "screen", "backlight", "ambient light"))
    return " ".join(aliases)


class SmartHomeHub:
    def __init__(self, providers: Iterable[SmartHomeProvider] | None = None):
        provider_items = (
            list(providers)
            if providers is not None
            else [
                SmartThingsProvider(),
                GoveeProvider(),
                NanoleafProvider(),
                TapoProvider(),
                LGThinQProvider(),
                PetlibroProvider(),
                MiHomeProvider(),
            ]
        )
        self.providers = {provider.name: provider for provider in provider_items}

    async def collect(
        self, owner_id: str, provider: str | None = None
    ) -> tuple[list[DeviceSnapshot], list[ProviderIssue]]:
        selected = _provider_name(provider)
        if selected and selected not in self.providers:
            raise ValueError(f"Unknown smart-home provider {provider!r}")
        candidates = (
            [self.providers[selected]] if selected else list(self.providers.values())
        )
        ready: list[SmartHomeProvider] = []
        issues: list[ProviderIssue] = []
        for item in candidates:
            try:
                configured, reason = item.configured(owner_id)
            except Exception as exc:
                configured, reason = False, str(exc)
            if configured:
                ready.append(item)
            else:
                issues.append(
                    ProviderIssue(item.name, reason or "Not configured", False)
                )
        results = await asyncio.gather(
            *(item.list_devices(owner_id) for item in ready), return_exceptions=True
        )
        devices: list[DeviceSnapshot] = []
        for item, result in zip(ready, results):
            if isinstance(result, Exception):
                issues.append(ProviderIssue(item.name, _safe_error(result)))
            else:
                devices.extend(result)
        return (
            sorted(devices, key=lambda item: (item.name.casefold(), item.provider)),
            issues,
        )

    @staticmethod
    def _matches(devices: list[DeviceSnapshot], target: str) -> list[DeviceSnapshot]:
        query = _normalize(target)
        if not query:
            return devices
        exact = [
            item
            for item in devices
            if query
            in {
                _normalize(item.name),
                _normalize(item.device_id),
                _normalize(item.key),
            }
        ]
        if exact:
            return exact
        query_tokens = set(query.split())
        matches = []
        for item in devices:
            searchable = _normalize(
                " ".join(
                    (
                        item.name,
                        item.device_id,
                        item.device_type,
                        item.provider,
                        _semantic_aliases(item),
                    )
                )
            )
            if query in searchable or query_tokens <= set(searchable.split()):
                matches.append(item)
        return matches

    async def status(
        self, owner_id: str, target: str | None = None, provider: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        devices, issues = await self.collect(owner_id, provider)
        matches = self._matches(devices, target or "")
        if target and not matches:
            names = ", ".join(item.name for item in devices[:12])
            text = f"I couldn't find a smart-home device matching {target!r}."
            if names:
                text += f" Available devices: {names}."
            elif issues:
                text += " No configured provider returned a device."
            return text, self._data(devices, issues)
        shown = matches if target else devices
        data = self._data(shown, issues)
        if not shown:
            missing = ", ".join(
                issue.provider for issue in issues if not issue.configured
            )
            text = "No smart-home devices are available to Curie yet."
            if missing:
                text += f" Configure at least one provider ({missing}); see docs/SMART_HOME_INTEGRATIONS.md."
            return text, data
        return self._format_summary(shown, issues, scoped=bool(target)), data

    async def control(
        self,
        owner_id: str,
        target: str,
        state: str,
        provider: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        state = require_power_state(state)
        if _normalize(target) in {
            "all",
            "everything",
            "home",
            "house",
            "every device",
            "all devices",
        }:
            raise ValueError(
                "Bulk whole-home power changes are not accepted from a simple command; name one device"
            )
        devices, issues = await self.collect(owner_id, provider)
        matches = self._matches([item for item in devices if item.controllable], target)
        if not matches:
            offline = self._matches(devices, target)
            if offline:
                raise ValueError(
                    f"{offline[0].name} does not expose safe on/off control"
                )
            available = ", ".join(item.name for item in devices if item.controllable)
            message = f"I couldn't find one controllable device matching {target!r}."
            if available:
                message += f" Controllable devices: {available}."
            if not devices and issues:
                message += " No configured provider returned a device."
            raise LookupError(message)
        if len(matches) > 1:
            online_matches = [item for item in matches if item.online is True]
            if len(online_matches) == 1:
                matches = online_matches
        if len(matches) > 1:
            choices = ", ".join(
                f"{item.name} ({item.provider})" for item in matches[:10]
            )
            raise ValueError(
                f"That target is ambiguous. Please name one of: {choices}."
            )
        device = matches[0]
        if device.online is False:
            raise ConnectionError(
                f"I can't reach {device.name} right now. It's offline, so I didn't send the command."
            )
        if device.online is True and device.power == state:
            receipt = ControlReceipt(
                device.provider,
                device.device_id,
                device.name,
                state,
                state,
                device,
            )
            return (
                f"The {device.name} is already {state}, monsieur.",
                {"receipt": receipt.as_dict(), "already_in_state": True},
            )
        receipt = await self.providers[device.provider].set_power(
            owner_id, device.device_id, state
        )
        if receipt.verified_state == state:
            text = f"Done. {receipt.name} is now {state}."
        elif receipt.verified_state in {"on", "off"}:
            text = f"Not quite. I asked {receipt.name} to turn {state}, but it still reports {receipt.verified_state}."
        else:
            text = f"The command was accepted, but I couldn't verify that {receipt.name} is {state} yet."
        return text, {"receipt": receipt.as_dict()}

    @staticmethod
    def _data(
        devices: list[DeviceSnapshot], issues: list[ProviderIssue]
    ) -> dict[str, Any]:
        return {
            "devices": [item.as_dict() for item in devices],
            "issues": [item.as_dict() for item in issues],
            "counts": {
                "total": len(devices),
                "on": sum(item.power == "on" for item in devices),
                "off": sum(item.power == "off" for item in devices),
                "offline": sum(item.online is False for item in devices),
                "unknown_power": sum(item.power == "unknown" for item in devices),
                "running": sum(item.running is True for item in devices),
            },
            "insights": SmartHomeHub._insights(devices),
        }

    @staticmethod
    def _insights(devices: list[DeviceSnapshot]) -> list[str]:
        insights = []
        offline = [item.name for item in devices if item.online is False]
        if offline:
            insights.append("Offline: " + ", ".join(offline))
        for item in devices:
            for metric, value in item.metrics.items():
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                label = metric.casefold()
                if (
                    any(
                        word in label for word in ("battery", "filter", "food", "water")
                    )
                    and numeric < 20
                ):
                    insights.append(
                        f"{item.name} has low {metric.replace('_', ' ')} ({value})"
                    )
        return insights

    @staticmethod
    def _format_summary(
        devices: list[DeviceSnapshot], issues: list[ProviderIssue], *, scoped: bool
    ) -> str:
        on = [item for item in devices if item.power == "on"]
        off = [item for item in devices if item.power == "off"]
        offline = [item for item in devices if item.online is False]
        unknown = [
            item
            for item in devices
            if item.power == "unknown" and item.online is not False
        ]
        heading = "Device status" if scoped else "Home status"
        lines = [
            f"{heading}: {len(on)} on, {len(off)} off, {len(offline)} offline, {len(unknown)} with unknown power state."
        ]
        for item in devices:
            connectivity = "offline" if item.online is False else item.power
            metric_text = ", ".join(
                f"{key.replace('_', ' ')}: {value}"
                for key, value in list(item.metrics.items())[:4]
            )
            suffix = f" — {metric_text}" if metric_text else ""
            lines.append(f"- {item.name} [{item.provider}]: {connectivity}{suffix}")
        insights = SmartHomeHub._insights(devices)
        if insights:
            lines.append("Attention: " + "; ".join(insights))
        failed = [issue.provider for issue in issues if issue.configured]
        if failed:
            lines.append("Unavailable during this check: " + ", ".join(failed) + ".")
        return "\n".join(lines)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "Request timed out"
    if isinstance(exc, http_errors()):
        return f"Provider request failed ({exc.__class__.__name__})"
    return str(exc)[:300]


def http_errors() -> tuple[type[Exception], ...]:
    try:
        import httpx

        return (httpx.HTTPError,)
    except ImportError:
        return ()


_hub: SmartHomeHub | None = None


def get_smart_home_hub() -> SmartHomeHub:
    global _hub
    if _hub is None:
        _hub = SmartHomeHub()
    return _hub


def reset_smart_home_hub() -> None:
    global _hub
    _hub = None
