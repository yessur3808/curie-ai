"""Thread-safe canonical device inventory with bounded refresh and staleness."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import os
import re
import threading
from typing import Iterable

from .aliases import list_device_aliases, normalize_alias
from .base import SmartHomeProvider
from .models import CanonicalDevice, DeviceSnapshot, ProviderIssue

_SECRET_ERROR_VALUE = re.compile(
    r"(?i)(bearer\s+)\S+|((?:token|password|secret|api[_-]?key|authorization)\s*[:=]\s*)\S+"
)


def _safe_issue_message(value: object) -> str:
    return _SECRET_ERROR_VALUE.sub(
        lambda match: (match.group(1) or match.group(2) or "") + "[REDACTED]",
        str(value),
    )[:300]


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    devices: tuple[CanonicalDevice, ...]
    issues: tuple[ProviderIssue, ...]
    refreshed_at: str
    from_cache: bool = False


class DeviceInventoryService:
    def __init__(
        self,
        providers: Iterable[SmartHomeProvider],
        *,
        refresh_seconds: float | None = None,
        stale_seconds: float | None = None,
    ):
        self.providers = {item.name: item for item in providers}
        self.refresh_seconds = max(
            1.0,
            float(
                refresh_seconds
                if refresh_seconds is not None
                else os.getenv("HOME_INVENTORY_REFRESH_SECONDS", "30")
            ),
        )
        self.stale_seconds = max(
            self.refresh_seconds,
            float(
                stale_seconds
                if stale_seconds is not None
                else os.getenv("HOME_INVENTORY_STALE_SECONDS", "300")
            ),
        )
        self._devices: dict[tuple[str, str, str], CanonicalDevice] = {}
        self._issues: dict[tuple[str, str], ProviderIssue] = {}
        self._refreshed: dict[tuple[str, str], datetime] = {}
        self._async_lock = asyncio.Lock()
        self._read_lock = threading.RLock()

    def _snapshot(
        self, owner_id: str, provider: str | None, *, cached: bool
    ) -> InventorySnapshot:
        with self._read_lock:
            devices = tuple(
                sorted(
                    (
                        item
                        for (owner, provider_name, _), item in self._devices.items()
                        if owner == owner_id
                        and (not provider or provider_name == provider)
                    ),
                    key=lambda item: (item.display_name.casefold(), item.provider),
                )
            )
            issues = tuple(
                item
                for (owner, provider_name), item in self._issues.items()
                if owner == owner_id and (not provider or provider_name == provider)
            )
            refreshed = max(
                (
                    value
                    for (owner, provider_name), value in self._refreshed.items()
                    if owner == owner_id and (not provider or provider_name == provider)
                ),
                default=datetime.now(timezone.utc),
            )
        return InventorySnapshot(devices, issues, refreshed.isoformat(), cached)

    def read(self, owner_id: str, provider: str | None = None) -> InventorySnapshot:
        return self._snapshot(str(owner_id), provider, cached=True)

    async def refresh(
        self,
        owner_id: str,
        provider: str | None = None,
        *,
        force: bool = False,
    ) -> InventorySnapshot:
        owner = str(owner_id)
        selected = (
            [self.providers[provider]] if provider else list(self.providers.values())
        )
        if not selected:
            return self._snapshot(owner, provider, cached=True)
        now = datetime.now(timezone.utc)
        with self._read_lock:
            fresh = all(
                now
                - self._refreshed.get(
                    (owner, item.name), datetime.min.replace(tzinfo=timezone.utc)
                )
                < timedelta(seconds=self.refresh_seconds)
                for item in selected
            )
        if fresh and not force:
            return self._snapshot(owner, provider, cached=True)
        async with self._async_lock:
            # Another caller may have completed the same refresh while this one
            # was waiting for the lock. Avoid immediately querying every
            # provider a second time.
            now = datetime.now(timezone.utc)
            with self._read_lock:
                fresh = all(
                    now
                    - self._refreshed.get(
                        (owner, item.name),
                        datetime.min.replace(tzinfo=timezone.utc),
                    )
                    < timedelta(seconds=self.refresh_seconds)
                    for item in selected
                )
            if fresh and not force:
                return self._snapshot(owner, provider, cached=True)

            aliases = list_device_aliases(owner)
            configured_providers: list[SmartHomeProvider] = []
            for item in selected:
                try:
                    configured, reason = item.configured(owner)
                except Exception as exc:
                    configured, reason = False, _safe_issue_message(exc)
                if not configured:
                    with self._read_lock:
                        self._issues[(owner, item.name)] = ProviderIssue(
                            item.name,
                            _safe_issue_message(reason or "Not configured"),
                            False,
                        )
                        self._mark_provider_unavailable(owner, item.name)
                        self._refreshed[(owner, item.name)] = now
                    continue
                configured_providers.append(item)

            # Provider calls are independent network operations. Run them
            # concurrently so one slow integration does not serially delay all
            # other inventory sources.
            results = await asyncio.gather(
                *(item.list_devices(owner) for item in configured_providers),
                return_exceptions=True,
            )
            observed_at = datetime.now(timezone.utc)
            for item, result in zip(configured_providers, results):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, Exception):
                    with self._read_lock:
                        self._issues[(owner, item.name)] = ProviderIssue(
                            item.name, _safe_issue_message(result)
                        )
                        self._mark_provider_unavailable(owner, item.name)
                        self._refreshed[(owner, item.name)] = observed_at
                    continue
                snapshots = result
                seen: set[str] = set()
                with self._read_lock:
                    self._issues.pop((owner, item.name), None)
                    for snapshot in snapshots:
                        seen.add(snapshot.device_id)
                        confirmed = tuple(
                            alias.alias
                            for alias in aliases
                            if alias.status == "confirmed"
                            and alias.device_key == snapshot.key
                            and not alias.expired
                        )
                        rejected = tuple(
                            alias.alias
                            for alias in aliases
                            if alias.status == "rejected"
                        )
                        self._devices[(owner, item.name, snapshot.device_id)] = (
                            CanonicalDevice.from_snapshot(
                                snapshot,
                                normalized_name=normalize_alias(snapshot.name),
                                confirmed_aliases=confirmed,
                                rejected_aliases=rejected,
                            )
                        )
                    for key, current in tuple(self._devices.items()):
                        if (
                            key[0] == owner
                            and key[1] == item.name
                            and key[2] not in seen
                        ):
                            self._devices[key] = replace(
                                current,
                                inventory_available=False,
                                online_status=False,
                                provider_metadata={
                                    **dict(current.provider_metadata),
                                    "inventory_status": "unavailable",
                                },
                            )
                    self._refreshed[(owner, item.name)] = observed_at
            self._drop_expired_stale(owner, observed_at)
        return self._snapshot(owner, provider, cached=False)

    def _mark_provider_unavailable(self, owner: str, provider: str) -> None:
        for key, current in tuple(self._devices.items()):
            if key[0] == owner and key[1] == provider:
                self._devices[key] = replace(
                    current,
                    inventory_available=False,
                    online_status=False,
                    provider_metadata={
                        **dict(current.provider_metadata),
                        "inventory_status": "provider_unavailable",
                    },
                )

    def _drop_expired_stale(self, owner: str, now: datetime) -> None:
        for key, current in tuple(self._devices.items()):
            if key[0] != owner or current.inventory_available:
                continue
            try:
                observed = datetime.fromisoformat(
                    current.last_observed_at.replace("Z", "+00:00")
                )
            except ValueError:
                observed = now
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            if now - observed > timedelta(seconds=self.stale_seconds):
                self._devices.pop(key, None)

    def observe(self, owner_id: str, snapshot: DeviceSnapshot | None) -> None:
        if snapshot is None:
            return
        owner = str(owner_id)
        aliases = list_device_aliases(owner)
        confirmed = tuple(
            alias.alias
            for alias in aliases
            if alias.status == "confirmed"
            and alias.device_key == snapshot.key
            and not alias.expired
        )
        rejected = tuple(alias.alias for alias in aliases if alias.status == "rejected")
        canonical = CanonicalDevice.from_snapshot(
            snapshot,
            normalized_name=normalize_alias(snapshot.name),
            confirmed_aliases=confirmed,
            rejected_aliases=rejected,
        )
        with self._read_lock:
            self._devices[(owner, snapshot.provider, snapshot.device_id)] = canonical
