"""Provider-neutral, single-use storage for OAuth authorization state."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
from typing import Any, Mapping

from memory.local_store import _LOCK, _managed_connection, save_personal_item


_STATE_KIND = "oauth_state"
_MAX_RECENT_STATES = 100


def save_oauth_state(owner_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one short-lived state document behind a service boundary."""
    item = dict(document)
    if not owner_id or not item.get("state") or not item.get("provider"):
        raise ValueError("OAuth state requires owner, state, and provider")
    return save_personal_item(str(owner_id), _STATE_KIND, item)


def consume_oauth_state(state: str, *, provider: str) -> tuple[str, dict[str, Any]]:
    """Atomically consume a matching unexpired provider state exactly once."""
    candidate = str(state or "")
    expected_provider = str(provider or "").strip().casefold()
    if not candidate or len(candidate) > 512 or not expected_provider:
        raise PermissionError("OAuth state is invalid, expired, or already used")

    now = datetime.now(timezone.utc)
    with _LOCK, _managed_connection() as connection:
        rows = connection.execute(
            "SELECT id,internal_id,document_json FROM personal_items "
            "WHERE kind=? ORDER BY updated_at DESC LIMIT ?",
            (_STATE_KIND, _MAX_RECENT_STATES),
        ).fetchall()
        for row in rows:
            try:
                item = json.loads(row["document_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not secrets.compare_digest(str(item.get("state", "")), candidate):
                continue
            valid = (
                str(item.get("provider") or "").casefold() == expected_provider
                and not item.get("used")
                and float(item.get("expires_at", 0)) > now.timestamp()
            )
            if not valid:
                break
            item["used"] = True
            updated = connection.execute(
                "UPDATE personal_items SET document_json=?,updated_at=? "
                "WHERE id=? AND internal_id=? AND kind=? AND document_json=?",
                (
                    json.dumps(item, default=str),
                    now.isoformat(),
                    str(row["id"]),
                    str(row["internal_id"]),
                    _STATE_KIND,
                    str(row["document_json"]),
                ),
            )
            if updated.rowcount == 1:
                return str(row["internal_id"]), item
            break

    raise PermissionError("OAuth state is invalid, expired, or already used")
