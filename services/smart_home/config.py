"""Configuration helpers that keep smart-home credentials out of tool output."""

from __future__ import annotations

import json
import os
from typing import Any


def json_list(name: str) -> list[dict[str, Any]]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must contain valid JSON") from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        raise ValueError(f"{name} must be a JSON array of objects")
    return [dict(item) for item in parsed]


def owner_credentials(owner_id: str, provider: str) -> dict[str, Any]:
    """Load an encrypted owner record when the vault is enabled."""
    if not os.getenv("CURIE_CREDENTIAL_KEY", "").strip():
        return {}
    try:
        from services.credential_vault import get_credential

        return get_credential(owner_id, f"smart_home:{provider}") or {}
    except (PermissionError, RuntimeError):
        return {}


def setting(
    owner_id: str,
    provider: str,
    field: str,
    env_name: str,
    default: Any = None,
) -> Any:
    credentials = owner_credentials(owner_id, provider)
    value = credentials.get(field)
    if value not in (None, ""):
        return value
    value = os.getenv(env_name)
    return value if value not in (None, "") else default
