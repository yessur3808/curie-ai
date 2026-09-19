"""Dialogue-state device reference helpers."""

from __future__ import annotations

from collections.abc import Iterable

from services.smart_home.models import CanonicalDevice


def recent_canonical_devices(
    canonical_ids: Iterable[str], devices: Iterable[CanonicalDevice]
) -> tuple[CanonicalDevice, ...]:
    """Return recent canonical devices in dialogue order without guessing names."""
    by_id = {item.canonical_id: item for item in devices}
    return tuple(by_id[item] for item in canonical_ids if item in by_id)
