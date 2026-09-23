"""Aggregate discovery, analysis, target resolution, and control receipts."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable
from typing import Any

from .base import SmartHomeProvider, require_power_state
from .cloud import GoveeProvider, SmartThingsProvider
from .lg import LGThinQProvider
from .local import MiHomeProvider, NanoleafProvider, PetlibroProvider, TapoProvider
from agent.understanding.entities import DeviceResolver, ResolutionStatus

from .aliases import (
    DeviceAlias,
    confirm_device_alias,
    list_device_aliases,
    normalize_alias,
    record_alias_candidate,
    reject_device_alias,
)
from .inventory import DeviceInventoryService
from .models import CanonicalDevice, ControlReceipt, DeviceSnapshot, ProviderIssue

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


def _owner_aliases(owner_id: str) -> list[DeviceAlias]:
    try:
        return list_device_aliases(str(owner_id))
    except Exception:
        return []


def _join_names(names: list[str]) -> str:
    if len(names) < 2:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _group_kind(target: str) -> str | None:
    """Recognize bounded natural-language groups that are safe to resolve."""
    words = [
        word
        for word in _normalize(target).split()
        if word not in {"all", "every", "each", "both", "the", "my", "our"}
    ]
    if words in (["light"], ["lights"], ["lamp"], ["lamps"], ["bulb"], ["bulbs"]):
        return "lights"
    return None


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
        self.inventory = DeviceInventoryService(provider_items)
        self.resolver = DeviceResolver()

    async def collect(
        self, owner_id: str, provider: str | None = None
    ) -> tuple[list[DeviceSnapshot], list[ProviderIssue]]:
        selected = _provider_name(provider)
        if selected and selected not in self.providers:
            raise ValueError(f"Unknown smart-home provider {provider!r}")
        snapshot = await self.inventory.refresh(str(owner_id), selected)
        return (
            [item.to_snapshot() for item in snapshot.devices],
            list(snapshot.issues),
        )

    async def canonical_inventory(
        self, owner_id: str, provider: str | None = None, *, force: bool = False
    ) -> tuple[list[CanonicalDevice], list[ProviderIssue]]:
        selected = _provider_name(provider)
        if selected and selected not in self.providers:
            raise ValueError(f"Unknown smart-home provider {provider!r}")
        snapshot = await self.inventory.refresh(str(owner_id), selected, force=force)
        return list(snapshot.devices), list(snapshot.issues)

    async def status(
        self,
        owner_id: str,
        target: str | None = None,
        provider: str | None = None,
        *,
        match_only: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        canonical, issues = await self.canonical_inventory(owner_id, provider)
        devices = [item.to_snapshot() for item in canonical]
        resolution = (
            self.resolver.resolve(
                target,
                canonical,
                aliases=_owner_aliases(owner_id),
                explicit_provider=_provider_name(provider),
                consequential=False,
            )
            if target
            else None
        )
        matches = (
            [item.to_snapshot() for item in resolution.devices]
            if resolution and resolution.resolved
            else []
        )
        if target and (not resolution or not resolution.resolved):
            names = ", ".join(item.name for item in devices[:12])
            if resolution and resolution.status is ResolutionStatus.REJECTED_ALIAS:
                text = (
                    f"You previously corrected {target!r} as not being a device, "
                    "so I did not map it to anything."
                )
            elif resolution and resolution.status is ResolutionStatus.AMBIGUOUS:
                text = (
                    f"{target!r} could mean more than one device: "
                    + ", ".join(resolution.candidates)
                    + "."
                )
            else:
                text = f"I couldn't find a smart-home device matching {target!r}."
            if names:
                text += f" Available devices: {names}."
            elif issues:
                text += " No configured provider returned a device."
            return text, self._data(devices, issues)
        shown = matches if target else devices
        data = self._data(shown, issues)
        if resolution:
            data["resolution"] = {
                "confidence": resolution.confidence,
                "reason": resolution.reason,
            }
        if not shown:
            missing = ", ".join(
                issue.provider for issue in issues if not issue.configured
            )
            text = "No smart-home devices are available to Curie yet."
            if missing:
                text += f" Configure at least one provider ({missing}); see docs/SMART_HOME_INTEGRATIONS.md."
            return text, data
        if match_only and target:
            if len(shown) == 1:
                device = shown[0]
                text = f"{target!r} matches {device.name}."
                if device.online is False:
                    text += " It's currently offline."
                elif device.power in {"on", "off"}:
                    text += f" It's currently {device.power}."
                return text, data
            return (
                f"{target!r} matches {len(shown)} devices: "
                + _join_names([item.name for item in shown])
                + ".",
                data,
            )
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
        }:
            raise ValueError(
                "Bulk whole-home power changes are not accepted from a simple command; name one device"
            )
        canonical, issues = await self.canonical_inventory(
            owner_id, provider, force=True
        )
        aliases = _owner_aliases(owner_id)
        resolution = self.resolver.resolve(
            target,
            canonical,
            aliases=aliases,
            explicit_provider=_provider_name(provider),
            consequential=True,
        )
        if resolution.status is ResolutionStatus.GROUP:
            return await self.control_group(owner_id, target, state, provider)
        if not resolution.resolved:
            if resolution.status is ResolutionStatus.REJECTED_ALIAS:
                raise LookupError(
                    f"You corrected {target!r} as not being a device, so I did not control anything."
                )
            if resolution.status is ResolutionStatus.AMBIGUOUS:
                raise ValueError(
                    f"That target is ambiguous. Please name one of: {', '.join(resolution.candidates)}."
                )
            available = ", ".join(
                item.display_name for item in canonical if item.controllable
            )
            message = f"I couldn't find one controllable device matching {target!r}."
            if available:
                message += f" Controllable devices: {available}."
            if not canonical and issues:
                message += " No configured provider returned a device."
            raise LookupError(message)
        device_model = resolution.devices[0]
        if not device_model.controllable:
            raise ValueError(
                f"{device_model.display_name} does not expose safe on/off control"
            )
        device = device_model.to_snapshot()
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
                {
                    "receipt": receipt.as_dict(),
                    "already_in_state": True,
                    "verification_status": "already_satisfied",
                    "resolution": {
                        "confidence": resolution.confidence,
                        "reason": resolution.reason,
                    },
                },
            )
        receipt = await self.providers[device.provider].set_power(
            owner_id, device.device_id, state
        )
        self.inventory.observe(owner_id, receipt.snapshot)
        if receipt.verified_state == state:
            text = f"Done. {receipt.name} is now {state}."
            verification_status = "verified"
        elif receipt.verified_state in {"on", "off"}:
            text = f"Not quite. I asked {receipt.name} to turn {state}, but it still reports {receipt.verified_state}."
            verification_status = "contradicted"
        else:
            text = f"The command was accepted, but I couldn't verify that {receipt.name} is {state} yet."
            verification_status = "unverified"
        if resolution.reason == "strong_fuzzy_name":
            record_alias_candidate(
                owner_id,
                alias=target,
                device_key=device.key,
                device_name=device.name,
            )
        data = {
            "receipt": receipt.as_dict(),
            "verification_status": verification_status,
            "resolution": {
                "confidence": resolution.confidence,
                "reason": resolution.reason,
            },
        }
        if device.power in {"on", "off"} and device.power != state:
            data["pre_state"] = {
                "target": device.key,
                "state": device.power,
                "provider": device.provider,
            }
        return text, data

    async def control_group(
        self,
        owner_id: str,
        target: str,
        state: str,
        provider: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve and control every device in one bounded semantic category."""
        state = require_power_state(state)
        canonical, issues = await self.canonical_inventory(
            owner_id, provider, force=True
        )
        resolution = self.resolver.resolve(
            target,
            canonical,
            aliases=_owner_aliases(owner_id),
            explicit_provider=_provider_name(provider),
            consequential=True,
        )
        if resolution.status is not ResolutionStatus.GROUP:
            raise ValueError(f"{target!r} is not a recognized device group")
        grouped = list(resolution.devices)
        selected = [item for item in grouped if item.controllable]
        if not selected:
            if grouped:
                raise ValueError(
                    f"I found {_join_names([item.display_name for item in grouped])}, but "
                    "none exposes safe on/off control."
                )
            available = [item.display_name for item in canonical if item.controllable]
            message = f"I couldn't find any controllable devices matching {target!r}."
            if available:
                message += f" Other controllable devices: {_join_names(available)}."
            elif issues:
                message += " No configured provider returned a controllable device."
            raise LookupError(message)
        if len(selected) > 32:
            raise ValueError(
                f"I found {len(selected)} matching devices. Please narrow the room or group before I control them."
            )
        if len(selected) == 1:
            text, data = await self.control(
                owner_id, selected[0].canonical_id, state, provider
            )
        else:
            text, data = await self.control_many(
                owner_id, [item.canonical_id for item in selected], state, provider
            )
        data["group"] = {
            "kind": _group_kind(target) or "capability_group",
            "target": target,
            "device_names": [item.display_name for item in selected],
            "canonical_ids": [item.canonical_id for item in selected],
        }
        return text, data

    async def control_many(
        self,
        owner_id: str,
        targets: list[str],
        state: str,
        provider: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Resolve an entire device set first, then execute it as one plan."""
        state = require_power_state(state)
        clean_targets = [str(item).strip() for item in targets if str(item).strip()]
        if not 1 < len(clean_targets) <= 32:
            raise ValueError("Name between two and 32 devices for a grouped command")
        blocked = {"all", "everything", "home", "house", "every device", "all devices"}
        if any(_normalize(target) in blocked for target in clean_targets):
            raise ValueError(
                "Bulk whole-home power changes are not accepted; name each device"
            )

        canonical, issues = await self.canonical_inventory(
            owner_id, provider, force=True
        )
        aliases = _owner_aliases(owner_id)
        plan: list[tuple[str, DeviceSnapshot, float, str]] = []
        for target in clean_targets:
            resolution = self.resolver.resolve(
                target,
                canonical,
                aliases=aliases,
                explicit_provider=_provider_name(provider),
                consequential=True,
            )
            if not resolution.resolved:
                if resolution.status is ResolutionStatus.REJECTED_ALIAS:
                    raise LookupError(
                        f"You corrected {target!r} as not being a device, so I did not control anything."
                    )
                if resolution.status is ResolutionStatus.AMBIGUOUS:
                    raise ValueError(
                        f"{target!r} could mean more than one device. Which one: "
                        + ", ".join(resolution.candidates)
                        + "?"
                    )
                available = ", ".join(
                    item.display_name for item in canonical if item.controllable
                )
                message = f"I couldn't match {target!r} to a controllable device."
                if available:
                    message += f" Controllable devices: {available}."
                if not canonical and issues:
                    message += " No configured provider returned a device."
                raise LookupError(message)
            for resolved in resolution.devices:
                if not resolved.controllable:
                    raise ValueError(
                        f"{resolved.display_name} does not expose safe on/off control"
                    )
                device = resolved.to_snapshot()
                if all(existing.key != device.key for _, existing, _, _ in plan):
                    plan.append(
                        (target, device, resolution.confidence, resolution.reason)
                    )

        offline = [device.name for _, device, _, _ in plan if device.online is False]
        if offline:
            raise ConnectionError(
                f"I can't reach {_join_names(offline)} right now, so I didn't send any of the commands."
            )

        receipts: list[ControlReceipt] = []
        already_keys: set[str] = set()
        pending: list[tuple[DeviceSnapshot, Any]] = []
        for _, device, _, _ in plan:
            if device.online is True and device.power == state:
                already_keys.add(device.key)
                receipts.append(
                    ControlReceipt(
                        device.provider,
                        device.device_id,
                        device.name,
                        state,
                        state,
                        device,
                    )
                )
            else:
                pending.append(
                    (
                        device,
                        self.providers[device.provider].set_power(
                            owner_id, device.device_id, state
                        ),
                    )
                )

        outcomes = await asyncio.gather(
            *(operation for _, operation in pending), return_exceptions=True
        )
        failures: list[str] = []
        for (device, _), outcome in zip(pending, outcomes):
            if isinstance(outcome, Exception):
                failures.append(f"{device.name}: {_safe_error(outcome)}")
            else:
                receipts.append(outcome)
                self.inventory.observe(owner_id, outcome.snapshot)

        receipt_by_key = {
            f"{item.provider}:{item.device_id}": item for item in receipts
        }
        already = [
            device.name for _, device, _, _ in plan if device.key in already_keys
        ]
        verified = [
            device.name
            for _, device, _, _ in plan
            if device.key not in already_keys
            and receipt_by_key.get(device.key)
            and receipt_by_key[device.key].verified_state == state
        ]
        contradicted = [
            device.name
            for _, device, _, _ in plan
            if receipt_by_key.get(device.key)
            and receipt_by_key[device.key].verified_state in {"on", "off"}
            and receipt_by_key[device.key].verified_state != state
        ]
        unverified = [
            device.name
            for _, device, _, _ in plan
            if receipt_by_key.get(device.key)
            and receipt_by_key[device.key].verified_state == "unknown"
        ]
        if len(already) == len(plan):
            verb = "is" if len(already) == 1 else "are"
            text = f"{_join_names(already)} {verb} already {state}, monsieur."
            aggregate = "all_already_satisfied"
            verification_status = "already_satisfied"
        elif len(verified) + len(already) == len(plan):
            if already:
                text = (
                    f"{_join_names(verified)} {'is' if len(verified) == 1 else 'are'} now {state}; "
                    f"{_join_names(already)} {'was' if len(already) == 1 else 'were'} already {state}."
                )
                aggregate = "verified_and_already_satisfied"
            else:
                verb = "is" if len(verified) == 1 else "are"
                text = f"Done. {_join_names(verified)} {verb} now {state}."
                aggregate = "all_verified"
            verification_status = "verified"
        else:
            parts = []
            if verified:
                parts.append(
                    f"{_join_names(verified)} {'is' if len(verified) == 1 else 'are'} {state}"
                )
            if already:
                verb = "was" if len(already) == 1 else "were"
                parts.append(f"{_join_names(already)} {verb} already {state}")
            if contradicted:
                parts.append(f"{_join_names(contradicted)} reported the opposite state")
            if unverified:
                parts.append(f"I couldn't verify {_join_names(unverified)}")
            if failures:
                parts.append("failed: " + "; ".join(failures))
            text = "; ".join(parts) + "."
            aggregate = (
                "all_failed"
                if failures and len(failures) == len(plan)
                else "partial_failure"
            )
            verification_status = (
                "failed"
                if failures
                else "contradicted" if contradicted else "unverified"
            )

        data = {
            "receipts": [item.as_dict() for item in receipts],
            "correlations": [
                {
                    "target": target,
                    "device_key": device.key,
                    "device_name": device.name,
                    "confidence": round(confidence, 3),
                    "reason": reason,
                }
                for target, device, confidence, reason in plan
            ],
            "failures": failures,
            "aggregate_status": aggregate,
            "verification_status": verification_status,
            "per_device": {
                "verified": verified,
                "already_satisfied": already,
                "contradicted": contradicted,
                "unverified": unverified,
                "failed": failures,
            },
        }
        changed = [device for device, _ in pending]
        previous_states = {device.power for device in changed}
        if changed and len(previous_states) == 1 and previous_states <= {"on", "off"}:
            previous_state = next(iter(previous_states))
            data["pre_state"] = (
                {
                    "target": changed[0].key,
                    "state": previous_state,
                    "provider": changed[0].provider,
                }
                if len(changed) == 1
                else {
                    "target": "",
                    "targets": [device.key for device in changed],
                    "state": previous_state,
                    "provider": "",
                }
            )
        return text, data

    async def learn_alias(
        self, owner_id: str, device_target: str, alias: str
    ) -> tuple[str, dict[str, Any]]:
        """Persist an explicit, owner-scoped mapping after resolving the device."""
        normalized_alias = normalize_alias(alias)
        if not normalized_alias or normalized_alias in {
            "it",
            "them",
            "this",
            "that",
            "device",
            "all",
            "everything",
        }:
            raise ValueError("That alias is too vague. Please use a specific nickname.")
        devices, _ = await self.canonical_inventory(owner_id, force=True)
        resolution = self.resolver.resolve(
            device_target,
            devices,
            aliases=_owner_aliases(owner_id),
            consequential=True,
        )
        if not resolution.resolved:
            names = ", ".join(item.display_name for item in devices[:12])
            message = f"I couldn't match {device_target!r} to a device."
            if names:
                message += f" Available devices: {names}."
            raise LookupError(message)
        if len(resolution.devices) > 1:
            choices = ", ".join(item.display_name for item in resolution.devices[:10])
            raise ValueError(f"{device_target!r} is ambiguous. Which one: {choices}?")
        device = resolution.devices[0]
        document = confirm_device_alias(
            str(owner_id),
            alias=normalized_alias,
            device_key=device.canonical_id,
            provider=device.provider,
            provider_id=device.provider_id,
            device_name=device.display_name,
        )
        return (
            f"Got it — {alias.strip()!r} means {device.display_name} from now on.",
            {"alias": document},
        )

    async def reject_alias(
        self, owner_id: str, alias: str
    ) -> tuple[str, dict[str, Any]]:
        """Apply an explicit correction as an immediate owner-scoped tombstone."""
        normalized = normalize_alias(alias)
        if not normalized:
            raise ValueError("Which device name should I stop using?")
        previous = next(
            (
                item
                for item in _owner_aliases(owner_id)
                if item.normalized_alias == normalized
                and item.status == "confirmed"
                and not item.expired
            ),
            None,
        )
        document = reject_device_alias(str(owner_id), normalized)
        data: dict[str, Any] = {
            "alias": document,
            "verification_status": "verified",
            "correction_applied": True,
        }
        if previous and previous.device_key:
            data["pre_state"] = {
                "device": previous.device_key,
                "alias": previous.alias,
            }
        return (
            f"Understood. I won't treat {alias.strip()!r} as a device name.",
            data,
        )

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


async def warm_smart_home_inventory(
    owner_ids: Iterable[str], *, timeout_seconds: float | None = None
) -> dict[str, dict[str, Any]]:
    """Warm each known owner's canonical inventory without exposing failures.

    Startup warming is bounded per owner and never makes a connector depend on
    one provider being healthy. Normal interval refreshes continue through the
    same inventory service after startup.
    """
    owners = tuple(
        dict.fromkeys(
            str(owner_id).strip() for owner_id in owner_ids if str(owner_id).strip()
        )
    )[:100]
    if not owners:
        return {}
    timeout = max(
        1.0,
        float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("HOME_INVENTORY_STARTUP_OWNER_TIMEOUT_SECONDS", "15")
        ),
    )
    hub = get_smart_home_hub()

    async def refresh_owner(owner_id: str) -> tuple[str, dict[str, Any]]:
        try:
            devices, issues = await asyncio.wait_for(
                hub.canonical_inventory(owner_id, force=True), timeout=timeout
            )
            return owner_id, {
                "status": "ready",
                "device_count": len(devices),
                "provider_issue_count": len(issues),
            }
        except (TimeoutError, asyncio.TimeoutError):
            return owner_id, {"status": "timeout"}
        except Exception as exc:
            return owner_id, {
                "status": "failed",
                "error_type": type(exc).__name__,
            }

    return dict(await asyncio.gather(*(refresh_owner(owner) for owner in owners)))


def get_smart_home_hub() -> SmartHomeHub:
    global _hub
    if _hub is None:
        _hub = SmartHomeHub()
    return _hub


def reset_smart_home_hub() -> None:
    global _hub
    _hub = None
