"""Privacy-safe, structured audit and security-event controls."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from utils.redaction import redact_text

_SECRET_KEY = re.compile(
    r"token|secret|password|passcode|credential|api.?key|authorization|cookie", re.I
)
_CONTENT_KEY = re.compile(
    r"message|content|prompt|body|result|output|error|summary|query|request|text|recipient|subject|title|contact",
    re.I,
)
_PATH_KEY = re.compile(r"path|file|root|directory", re.I)
_SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+\S+|(?:token|password|secret|api[_-]?key)\s*[:=]\s*\S+)"
)
SECURITY_THRESHOLDS = {
    "approval_failure": 5,
    "policy_denial": 10,
    "authentication_failure": 5,
    "secret_redaction": 3,
    "tool_failure": 10,
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def _safe_path(value: Any) -> str:
    path = Path(str(value))
    workspace = Path(os.getenv("CURIE_WORKSPACE_ROOT", Path.cwd())).resolve()
    try:
        return str(path.resolve().relative_to(workspace))
    except (OSError, ValueError):
        return f"[SENSITIVE_PATH]/{path.name}" if path.name else "[SENSITIVE_PATH]"


def redact(value: Any, key: str = "") -> Any:
    """Recursively remove credentials, personal contents, and sensitive paths."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k)[:80]: redact(v, str(k)) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set)):
        return [redact(item, key) for item in list(value)[:100]]
    if _PATH_KEY.search(key):
        return _safe_path(value)
    if _CONTENT_KEY.search(key) and value is not None:
        raw = str(value)
        return {"sha256": _digest(raw), "length": len(raw)}
    if isinstance(value, str):
        return redact_text(_SECRET_VALUE.sub("[REDACTED]", value))[:1000]
    return (
        value
        if value is None or isinstance(value, (bool, int, float))
        else str(value)[:1000]
    )


def normalize_audit_details(details: dict) -> dict:
    """Return the stable event envelope stored by every audit backend."""
    now = datetime.now(timezone.utc).isoformat()
    safe = redact(details)
    return {
        "schema_version": 1,
        "event_id": str(details.get("event_id") or uuid.uuid4().hex),
        "connector": str(details.get("connector") or "unknown")[:40],
        "validated_action": str(
            details.get("validated_action") or details.get("action") or "unknown"
        )[:120],
        "parameters": safe.get("parameters", safe.get("params", {})),
        "policy_decision": str(details.get("policy_decision") or "evaluated")[:80],
        "approval": redact(details.get("approval") or {"required": False}),
        "tool_version": str(details.get("tool_version") or "unknown")[:40],
        "started_at": str(details.get("started_at") or now),
        "finished_at": str(details.get("finished_at") or now),
        "changed_files": redact(details.get("changed_files") or [], "changed_files"),
        "command_exit_status": details.get("command_exit_status"),
        "citations": [
            _safe_citation(item) for item in details.get("citations", [])[:20]
        ],
        "outcome": safe.get("outcome", safe.get("result", safe.get("error", {}))),
        "verification_status": str(
            details.get("verification_status") or "not_recorded"
        )[:40],
        "security_category": details.get("security_category"),
    }


def _safe_citation(value: Any) -> str:
    try:
        parsed = urlsplit(str(value))
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, host + port, parsed.path[:300], "", ""))
    except (TypeError, ValueError):
        return "[INVALID_CITATION]"


def security_alerts(events: list[dict], window_minutes: int = 15) -> list[dict]:
    """Return categories meeting configured thresholds in a recent window."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, window_minutes))
    counts: Counter[str] = Counter()
    for event in events:
        try:
            created = datetime.fromisoformat(
                str(event.get("created_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        category = (event.get("details") or {}).get("security_category")
        if category in SECURITY_THRESHOLDS and created >= cutoff:
            counts[category] += 1
    return [
        {
            "category": category,
            "count": count,
            "threshold": SECURITY_THRESHOLDS[category],
            "severity": "warning",
        }
        for category, count in sorted(counts.items())
        if count >= SECURITY_THRESHOLDS[category]
    ]


def handle_audit_command(internal_id: str, text: str) -> str | None:
    """Owner-scoped inspection, JSON export, and explicit deletion."""
    command = text.strip().casefold()
    if not command.startswith("/audit"):
        return None
    from memory.repositories import get_repositories

    repo = get_repositories().audits
    if command in {"/audit", "/audit status"}:
        events = repo.list(str(internal_id), 1000)
        return f"Audit contains {len(events)} retained events. Active security alerts: {len(security_alerts(events))}."
    if command == "/audit export":
        return json.dumps(
            {"schema_version": 1, "events": repo.list(str(internal_id), 10000)},
            default=str,
            sort_keys=True,
        )
    if command == "/audit delete":
        return "Audit deletion is irreversible. Use `/audit delete confirm` to delete your audit records."
    if command == "/audit delete confirm":
        count = repo.delete_owner(str(internal_id))
        return f"Deleted {count} audit records belonging to your user identity."
    return "Use /audit, /audit export, or /audit delete."
