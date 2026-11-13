"""Read a bounded, credential-free view of Curie Observatory analytics.

The Observatory service already refreshes GA4 and writes its cache atomically.
Curie reads only the project map and cached aggregate reports; Google credentials,
the dashboard access key, and detailed per-page breakdowns never enter tool output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

_CONFIG_LIMIT = 1 << 20
_CACHE_LIMIT = 8 << 20
_SUPPORTED_DAYS = {7, 30, 90}


def observatory_data_dir() -> Path:
    configured = os.getenv("CURIE_OBSERVATORY_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Desktop" / "server" / "dashboard" / "data"


def observatory_analytics_available() -> tuple[bool, str | None]:
    root = observatory_data_dir()
    missing = [
        name
        for name in ("config.json", "analytics-cache.json")
        if not (root / name).is_file()
    ]
    if missing:
        return (
            False,
            f"Curie Observatory analytics files are unavailable: {', '.join(missing)}",
        )
    return True, None


def _read_object(path: Path, limit: int) -> dict[str, Any]:
    size = path.stat().st_size
    if size > limit:
        raise ValueError(f"{path.name} exceeds the bounded analytics read limit")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} does not contain a JSON object")
    return value


def _normalise(value: str) -> str:
    return " ".join(
        "".join(char.casefold() if char.isalnum() else " " for char in value).split()
    )


def _project_aliases(project: Mapping[str, Any]) -> set[str]:
    aliases = {
        _normalise(str(project.get("id") or "")),
        _normalise(str(project.get("name") or "")),
    }
    raw_url = str(project.get("url") or "").strip()
    if raw_url:
        host = (urlsplit(raw_url).hostname or "").removeprefix("www.")
        aliases.add(_normalise(host))
        if host:
            aliases.add(_normalise(host.split(".", 1)[0]))
    return {alias for alias in aliases if alias}


def _select_project(
    projects: list[dict[str, Any]], query: str
) -> dict[str, Any] | None:
    wanted = _normalise(query)
    if not wanted:
        return projects[0] if len(projects) == 1 else None

    exact = [project for project in projects if wanted in _project_aliases(project)]
    if len(exact) == 1:
        return exact[0]

    padded = f" {wanted} "
    matches = []
    for project in projects:
        aliases = _project_aliases(project)
        if any(len(alias) >= 3 and f" {alias} " in padded for alias in aliases):
            matches.append(project)
    return matches[0] if len(matches) == 1 else None


def read_project_analytics(
    query: str,
    *,
    days: int = 30,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Return one sanitized aggregate report from Observatory's local cache."""

    if days not in _SUPPORTED_DAYS:
        raise ValueError("Analytics range must be 7, 30, or 90 complete days")
    root = data_dir or observatory_data_dir()
    config = _read_object(root / "config.json", _CONFIG_LIMIT)
    cache = _read_object(root / "analytics-cache.json", _CACHE_LIMIT)
    projects = [item for item in config.get("projects", []) if isinstance(item, dict)]
    project = _select_project(projects, query)
    names = [
        str(item.get("name") or item.get("id") or "Unnamed project")
        for item in projects
    ]
    if project is None:
        return {
            "status": "project_required",
            "message": "Name the Observatory project whose analytics you want.",
            "projects": names,
        }

    name = str(project.get("name") or project.get("id") or "Project")
    project_id = str(project.get("id") or "")
    property_id = str(project.get("gaProperty") or "").strip()
    if not property_id:
        return {
            "status": "setup",
            "project": {"id": project_id, "name": name},
            "message": f"{name} is not connected to a GA4 property in Curie Observatory.",
        }

    cache_key = f"{property_id}:{days}"
    cached = cache.get(cache_key)
    report = cached.get("report") if isinstance(cached, dict) else None
    if not isinstance(report, dict):
        available_days = sorted(
            int(key.rsplit(":", 1)[1])
            for key in cache
            if key.startswith(property_id + ":") and key.rsplit(":", 1)[1].isdigit()
        )
        return {
            "status": "not_cached",
            "project": {"id": project_id, "name": name},
            "days": days,
            "availableDays": available_days,
            "message": f"No {days}-day Observatory analytics report is cached for {name} yet.",
        }

    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    previous = (
        report.get("previous") if isinstance(report.get("previous"), dict) else {}
    )
    date_range = report.get("range") if isinstance(report.get("range"), dict) else {}
    metric_names = (
        "screenPageViews",
        "activeUsers",
        "newUsers",
        "sessions",
        "engagedSessions",
        "eventCount",
    )
    return {
        "status": str(report.get("status") or "unknown"),
        "source": str(report.get("source") or "GA4"),
        "project": {"id": project_id, "name": name},
        "days": int(report.get("days") or days),
        "range": {
            key: date_range.get(key)
            for key in ("start", "end", "previousStart", "previousEnd")
            if date_range.get(key)
        },
        "totals": {
            key: totals.get(key) for key in metric_names if totals.get(key) is not None
        },
        "previous": {
            key: previous.get(key)
            for key in metric_names
            if previous.get(key) is not None
        },
        "updatedAt": report.get("updatedAt"),
        "attemptedAt": cached.get("attempt"),
        "timeZone": report.get("timeZone"),
        "message": report.get("message"),
    }


__all__ = [
    "observatory_analytics_available",
    "observatory_data_dir",
    "read_project_analytics",
]
