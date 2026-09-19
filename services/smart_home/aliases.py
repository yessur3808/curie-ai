"""Owner-scoped confirmed, rejected, and candidate device aliases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any


def normalize_alias(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


@dataclass(frozen=True, slots=True)
class DeviceAlias:
    alias: str
    normalized_alias: str
    status: str
    device_key: str | None
    provider: str | None
    provider_id: str | None
    device_name: str | None
    confidence: float
    provenance: str
    last_confirmed_at: str | None
    expires_at: str | None

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "DeviceAlias":
        alias = str(document.get("alias") or "")
        return cls(
            alias,
            normalize_alias(alias),
            str(document.get("status") or "confirmed"),
            str(document.get("device_key")) if document.get("device_key") else None,
            str(document.get("provider")) if document.get("provider") else None,
            str(document.get("device_id")) if document.get("device_id") else None,
            str(document.get("device_name")) if document.get("device_name") else None,
            float(document.get("confidence", 1.0)),
            str(document.get("provenance") or "legacy_explicit"),
            (
                str(document.get("last_confirmed_at"))
                if document.get("last_confirmed_at")
                else None
            ),
            str(document.get("expires_at")) if document.get("expires_at") else None,
        )

    @property
    def expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            value = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value <= datetime.now(timezone.utc)


def list_device_aliases(owner_id: str) -> list[DeviceAlias]:
    from memory.local_store import list_personal_items

    return [
        DeviceAlias.from_document(item)
        for item in list_personal_items(str(owner_id), "device_alias")
        if normalize_alias(str(item.get("alias") or ""))
    ]


def confirm_device_alias(
    owner_id: str,
    *,
    alias: str,
    device_key: str,
    provider: str,
    provider_id: str,
    device_name: str,
    provenance: str = "explicit_owner_instruction",
) -> dict[str, Any]:
    from memory.local_store import save_personal_item

    normalized = normalize_alias(alias)
    if not normalized:
        raise ValueError("Device alias cannot be empty")
    now = datetime.now(timezone.utc)
    return save_personal_item(
        str(owner_id),
        "device_alias",
        {
            "id": f"smart-home-alias:{normalized}",
            "alias": normalized,
            "status": "confirmed",
            "device_key": device_key,
            "provider": provider,
            "device_id": provider_id,
            "device_name": device_name,
            "confidence": 1.0,
            "provenance": provenance,
            "last_confirmed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=365)).isoformat(),
        },
    )


def reject_device_alias(
    owner_id: str,
    alias: str,
    *,
    provenance: str = "explicit_owner_correction",
) -> dict[str, Any]:
    from memory.local_store import save_personal_item

    normalized = normalize_alias(alias)
    if not normalized:
        raise ValueError("Rejected alias cannot be empty")
    return save_personal_item(
        str(owner_id),
        "device_alias",
        {
            "id": f"smart-home-alias:{normalized}",
            "alias": normalized,
            "status": "rejected",
            "confidence": 1.0,
            "provenance": provenance,
            "last_confirmed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": None,
        },
    )


def record_alias_candidate(
    owner_id: str,
    *,
    alias: str,
    device_key: str,
    device_name: str,
) -> dict[str, Any] | None:
    """Count a successful natural reference without promoting it to an alias."""
    from memory.local_store import list_personal_items, save_personal_item

    normalized = normalize_alias(alias)
    if not normalized:
        return None
    candidates = list_personal_items(str(owner_id), "device_alias_candidate")
    existing = next(
        (
            item
            for item in candidates
            if normalize_alias(str(item.get("alias") or "")) == normalized
            and str(item.get("device_key") or "") == device_key
        ),
        {},
    )
    count = int(existing.get("successful_references", 0)) + 1
    return save_personal_item(
        str(owner_id),
        "device_alias_candidate",
        {
            "id": f"device-alias-candidate:{normalized}:{device_key}",
            "alias": normalized,
            "device_key": device_key,
            "device_name": device_name,
            "successful_references": count,
            "status": "candidate",
            "promoted": False,
            "last_observed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        },
    )
