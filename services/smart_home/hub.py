"""Aggregate discovery, analysis, target resolution, and control receipts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from difflib import SequenceMatcher
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
    if "sync box" in name:
        aliases.extend(
            ("tv light", "television light", "screen light", "tv backlight", "ambient light")
        )
    return " ".join(aliases)


@dataclass(frozen=True, slots=True)
class _Match:
    device: DeviceSnapshot
    score: float
    reason: str


def _owner_aliases(owner_id: str) -> list[dict[str, Any]]:
    try:
        from memory.local_store import list_personal_items

        return list_personal_items(str(owner_id), "device_alias")
    except Exception:
        return []


def _device_for_alias(
    devices: list[DeviceSnapshot], alias: dict[str, Any]
) -> DeviceSnapshot | None:
    key = str(alias.get("device_key") or "")
    provider = str(alias.get("provider") or "")
    device_id = str(alias.get("device_id") or "")
    for device in devices:
        if key and device.key == key:
            return device
        if provider == device.provider and device_id == device.device_id:
            return device
    return None


def _join_names(names: list[str]) -> str:
    if len(names) < 2:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


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
    def _rank_matches(
        devices: list[DeviceSnapshot],
        target: str,
        aliases: list[dict[str, Any]] | None = None,
    ) -> list[_Match]:
        query = _normalize(target)
        if not query:
            return [_Match(item, 1.0, "all devices") for item in devices]

        canonical = []
        for item in devices:
            if query in {
                _normalize(item.name),
                _normalize(item.device_id),
                _normalize(item.key),
            }:
                canonical.append(_Match(item, 1.0, "exact device identity"))
        if canonical:
            return canonical

        learned: list[_Match] = []
        for alias in aliases or ():
            if _normalize(str(alias.get("alias") or "")) != query:
                continue
            device = _device_for_alias(devices, alias)
            if device and all(item.device.key != device.key for item in learned):
                learned.append(_Match(device, 0.99, "learned owner alias"))
        if learned:
            return learned

        query_tokens = set(query.split()) - {"the", "my", "a", "an", "device"}
        compact_query = query.replace(" ", "")
        ranked: list[_Match] = []
        for item in devices:
            name = _normalize(item.name)
            identity = _normalize(" ".join((item.name, item.device_id, item.key)))
            semantic = _normalize(_semantic_aliases(item))
            identity_tokens = set(identity.split())
            semantic_phrases = {
                phrase.strip()
                for phrase in re.split(r"\s{2,}|,", _semantic_aliases(item))
                if phrase.strip()
            }
            score, reason = 0.0, ""
            if query in name or name in query:
                score, reason = 0.94, "device-name phrase"
            elif query_tokens and query_tokens <= identity_tokens:
                score, reason = 0.90, "device-name tokens"
            elif query in semantic or query in semantic_phrases:
                score, reason = 0.86, "product-family alias"
            else:
                token_overlap = len(query_tokens & identity_tokens) / max(
                    len(query_tokens), 1
                )
                fuzzy = SequenceMatcher(
                    None, compact_query, name.replace(" ", "")
                ).ratio()
                if fuzzy >= 0.76:
                    score, reason = 0.72 + min((fuzzy - 0.76) * 0.5, 0.16), "close device name"
                elif token_overlap >= 0.67:
                    score, reason = 0.70 + token_overlap * 0.12, "partial device-name tokens"
            if score:
                ranked.append(_Match(item, score, reason))
        return sorted(
            ranked,
            key=lambda item: (
                item.score,
                item.device.online is True,
                item.device.controllable,
                item.device.name.casefold(),
            ),
            reverse=True,
        )

    @classmethod
    def _matches(
        cls,
        devices: list[DeviceSnapshot],
        target: str,
        aliases: list[dict[str, Any]] | None = None,
    ) -> list[DeviceSnapshot]:
        ranked = cls._rank_matches(devices, target, aliases)
        if not ranked or ranked[0].score < 0.72:
            return []
        best = ranked[0].score
        # A meaningful score gap is safe to auto-resolve; close candidates must
        # remain ambiguous instead of being guessed from incidental ordering.
        selected = [item.device for item in ranked if best - item.score < 0.08]
        return selected

    async def status(
        self, owner_id: str, target: str | None = None, provider: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        devices, issues = await self.collect(owner_id, provider)
        matches = self._matches(devices, target or "", _owner_aliases(owner_id))
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
        aliases = _owner_aliases(owner_id)
        matches = self._matches(
            [item for item in devices if item.controllable], target, aliases
        )
        if not matches:
            offline = self._matches(devices, target, aliases)
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
        if not 1 < len(clean_targets) <= 8:
            raise ValueError("Name between two and eight devices for a grouped command")
        blocked = {
            "all", "everything", "home", "house", "every device", "all devices"
        }
        if any(_normalize(target) in blocked for target in clean_targets):
            raise ValueError(
                "Bulk whole-home power changes are not accepted; name each device"
            )

        devices, issues = await self.collect(owner_id, provider)
        controllable = [item for item in devices if item.controllable]
        aliases = _owner_aliases(owner_id)
        plan: list[tuple[str, DeviceSnapshot, _Match]] = []
        for target in clean_targets:
            ranked = self._rank_matches(controllable, target, aliases)
            matches = self._matches(controllable, target, aliases)
            if not matches:
                non_control = self._matches(devices, target, aliases)
                if non_control:
                    raise ValueError(
                        f"{non_control[0].name} does not expose safe on/off control"
                    )
                available = ", ".join(item.name for item in controllable)
                message = f"I couldn't match {target!r} to a controllable device."
                if available:
                    message += f" Controllable devices: {available}."
                if not devices and issues:
                    message += " No configured provider returned a device."
                raise LookupError(message)
            if len(matches) > 1:
                choices = ", ".join(
                    f"{item.name} ({item.provider})" for item in matches[:10]
                )
                raise ValueError(
                    f"{target!r} could mean more than one device. Which one: {choices}?"
                )
            device = matches[0]
            detail = next(item for item in ranked if item.device.key == device.key)
            if all(existing.key != device.key for _, existing, _ in plan):
                plan.append((target, device, detail))

        offline = [device.name for _, device, _ in plan if device.online is False]
        if offline:
            raise ConnectionError(
                f"I can't reach {_join_names(offline)} right now, so I didn't send any of the commands."
            )

        receipts: list[ControlReceipt] = []
        pending: list[tuple[DeviceSnapshot, Any]] = []
        for _, device, _ in plan:
            if device.online is True and device.power == state:
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

        receipt_by_key = {
            f"{item.provider}:{item.device_id}": item for item in receipts
        }
        verified = [
            device.name
            for _, device, _ in plan
            if receipt_by_key.get(device.key)
            and receipt_by_key[device.key].verified_state == state
        ]
        unverified = [
            device.name
            for _, device, _ in plan
            if receipt_by_key.get(device.key)
            and receipt_by_key[device.key].verified_state != state
        ]
        all_already = not pending and len(verified) == len(plan)
        if all_already:
            text = f"{_join_names(verified)} are already {state}, monsieur."
        elif len(verified) == len(plan):
            text = f"Done. {_join_names(verified)} are now {state}."
        else:
            parts = []
            if verified:
                parts.append(f"{_join_names(verified)} {'is' if len(verified) == 1 else 'are'} {state}")
            if unverified:
                parts.append(f"I couldn't verify {_join_names(unverified)}")
            if failures:
                parts.append("failed: " + "; ".join(failures))
            text = "; ".join(parts) + "."

        return text, {
            "receipts": [item.as_dict() for item in receipts],
            "correlations": [
                {
                    "target": target,
                    "device_key": device.key,
                    "device_name": device.name,
                    "confidence": round(detail.score, 3),
                    "reason": detail.reason,
                }
                for target, device, detail in plan
            ],
            "failures": failures,
        }

    async def learn_alias(
        self, owner_id: str, device_target: str, alias: str
    ) -> tuple[str, dict[str, Any]]:
        """Persist an explicit, owner-scoped mapping after resolving the device."""
        normalized_alias = _normalize(alias)
        if not normalized_alias or normalized_alias in {
            "it", "them", "this", "that", "device", "all", "everything"
        }:
            raise ValueError("That alias is too vague. Please use a specific nickname.")
        devices, _ = await self.collect(owner_id)
        matches = self._matches(devices, device_target, _owner_aliases(owner_id))
        if not matches:
            names = ", ".join(item.name for item in devices[:12])
            message = f"I couldn't match {device_target!r} to a device."
            if names:
                message += f" Available devices: {names}."
            raise LookupError(message)
        if len(matches) > 1:
            choices = ", ".join(item.name for item in matches[:10])
            raise ValueError(
                f"{device_target!r} is ambiguous. Which one: {choices}?"
            )
        device = matches[0]
        from memory.local_store import save_personal_item

        document = save_personal_item(
            str(owner_id),
            "device_alias",
            {
                "id": f"smart-home-alias:{normalized_alias}",
                "alias": normalized_alias,
                "device_key": device.key,
                "provider": device.provider,
                "device_id": device.device_id,
                "device_name": device.name,
            },
        )
        return (
            f"Got it — {alias.strip()!r} means {device.name} from now on.",
            {"alias": document},
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


def get_smart_home_hub() -> SmartHomeHub:
    global _hub
    if _hub is None:
        _hub = SmartHomeHub()
    return _hub


def reset_smart_home_hub() -> None:
    global _hub
    _hub = None
