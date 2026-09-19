"""Owner-scoped personal operations with preview-first external writes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
import re
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from memory.local_store import list_personal_items, save_personal_item

HK_HOLIDAY_SOURCE = "https://www.1823.gov.hk/common/ical/en.json"
HK_HOLIDAY_AUTHORITY = "Hong Kong Digital Policy Office / 1823"
_UNTRUSTED_INSTRUCTION = re.compile(
    r"(?i)(ignore (?:previous|system)|run (?:this )?command|send (?:money|credentials)|"
    r"change permissions?|reveal (?:a )?(?:secret|token|password))"
)
_FORBIDDEN_ACCOUNT = re.compile(
    r"(?i)captcha|accept (?:the )?terms|invent identity|fake (?:name|address)|reuse password"
)


def add_birthday(
    owner_id: str,
    name: str,
    month: int,
    day: int,
    *,
    year: int | None = None,
    timezone_name: str = "UTC",
    lead_days: int = 7,
    provenance: str = "explicit_user",
) -> dict:
    """Store only explicitly supplied, private owner-scoped birthdays."""
    if provenance != "explicit_user":
        raise PermissionError("Birthdays may not be inferred")
    ZoneInfo(timezone_name)
    date(year or 2000, int(month), int(day))
    if not 0 <= int(lead_days) <= 60:
        raise ValueError("Birthday lead time must be between 0 and 60 days")
    return save_personal_item(
        owner_id,
        "birthday",
        {
            "name": str(name).strip()[:100],
            "month": int(month),
            "day": int(day),
            "year": int(year) if year else None,
            "timezone": timezone_name,
            "lead_days": int(lead_days),
            "provenance": provenance,
            "private": True,
        },
    )


def import_calendar_events(owner_id: str, events: list[dict], *, provider: str) -> int:
    """Cache normalized read-only OAuth calendar events without message bodies."""
    count = 0
    for raw in events[:500]:
        start = datetime.fromisoformat(str(raw["start"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(
            str(raw.get("end", raw["start"])).replace("Z", "+00:00")
        )
        save_personal_item(
            owner_id,
            "calendar_event",
            {
                "id": f"calendar-{provider}-{str(raw['id'])[:120]}",
                "title": str(raw.get("title") or "Busy")[:160],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "provider": provider[:40],
                "read_only": True,
                "source_ref": str(raw["id"])[:120],
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        count += 1
    return count


def sync_hk_holidays(
    owner_id: str, payload: dict, *, fetched_at: datetime | None = None
) -> int:
    """Persist a bounded response fetched from the fixed official 1823 source."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    calendar = payload.get("vcalendar")
    rows = (
        calendar.get("vevent", [])
        if isinstance(calendar, dict)
        else calendar if isinstance(calendar, list) else payload.get("events") or []
    )
    count = 0
    for raw in rows[:100]:
        if not isinstance(raw, dict):
            continue
        summary = raw.get("summary") or raw.get("title")
        start = raw.get("dtstart") or raw.get("date")
        if isinstance(summary, list):
            summary = summary[0]
        if isinstance(start, list):
            start = start[0]
        if isinstance(summary, dict):
            summary = summary.get("value")
        if isinstance(start, dict):
            start = start.get("value")
        if not summary or not start:
            continue
        parsed = datetime.strptime(str(start).replace("-", "")[:8], "%Y%m%d").date()
        save_personal_item(
            owner_id,
            "holiday",
            {
                "id": f"hk-holiday-{parsed.isoformat()}-{abs(hash(str(summary))) % 100000}",
                "title": str(summary)[:160],
                "date": parsed.isoformat(),
                "authority": HK_HOLIDAY_AUTHORITY,
                "source": HK_HOLIDAY_SOURCE,
                "refreshed_at": fetched_at.isoformat(),
            },
        )
        count += 1
    return count


def refresh_hk_holidays(owner_id: str, *, opener=urlopen) -> dict:
    """Fetch only the fixed authoritative JSON resource with strict bounds."""
    request = Request(
        HK_HOLIDAY_SOURCE,
        headers={"Accept": "application/json", "User-Agent": "Curie/1.0"},
    )
    with opener(request, timeout=10) as response:
        content_type = str(response.headers.get("Content-Type", "")).casefold()
        if "json" not in content_type:
            raise ValueError("Official holiday response is not JSON")
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("Official holiday response exceeds 1 MB")
    fetched_at = datetime.now(timezone.utc)
    count = sync_hk_holidays(owner_id, json.loads(body), fetched_at=fetched_at)
    return {
        "count": count,
        "source": HK_HOLIDAY_SOURCE,
        "authority": HK_HOLIDAY_AUTHORITY,
        "refreshed_at": fetched_at.isoformat(),
    }


def gregorian_to_islamic(value: date, locale: str = "en") -> dict:
    """Convert using the named arithmetic/tabular civil method."""
    y, m, d = value.year, value.month, value.day
    a = (14 - m) // 12
    yy = y + 4800 - a
    mm = m + 12 * a - 3
    jd = d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045
    day_count = jd - 1948440 + 10632
    n = (day_count - 1) // 10631
    day_count = day_count - 10631 * n + 354
    j = ((10985 - day_count) // 5316) * ((50 * day_count) // 17719) + (
        day_count // 5670
    ) * ((43 * day_count) // 15238)
    day_count = (
        day_count
        - ((30 - j) // 15) * ((17719 * j) // 50)
        - (j // 16) * ((15238 * j) // 43)
        + 29
    )
    month = (24 * day_count) // 709
    day = day_count - (709 * month) // 24
    year = 30 * n + j - 30
    return {
        "year": year,
        "month": month,
        "day": day,
        "method": "tabular-civil",
        "locale": locale,
        "calculated": True,
        "notice": "Calculated date; observed holidays may differ by authority and moon sighting.",
    }


def record_official_islamic_observance(
    owner_id: str, title: str, observed_date: date, *, authority: str, source: str
) -> dict:
    if not authority.strip() or not source.startswith("https://"):
        raise ValueError("An official authority and HTTPS source are required")
    return save_personal_item(
        owner_id,
        "holiday",
        {
            "title": title[:160],
            "date": observed_date.isoformat(),
            "authority": authority[:160],
            "source": source[:500],
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "calculated": False,
            "official_announcement": True,
        },
    )


def agenda(owner_id: str, start: date | None = None, days: int = 14) -> list[dict]:
    """Merge events, holidays, birthdays, and reminders, suppressing duplicates."""
    start = start or datetime.now(timezone.utc).date()
    end = start + timedelta(days=max(1, min(days, 31)))
    candidates: list[dict] = []
    for item in list_personal_items(owner_id):
        kind = "birthday" if item.get("private") and "month" in item else None
        if kind:
            for year in {start.year, end.year}:
                try:
                    event_date = date(year, item["month"], item["day"])
                except ValueError:
                    continue
                if start <= event_date <= end:
                    candidates.append(
                        {
                            "kind": "birthday",
                            "title": f"{item['name']}'s birthday",
                            "at": event_date.isoformat(),
                            "source": item["provenance"],
                        }
                    )
        elif item.get("date"):
            event_date = date.fromisoformat(item["date"])
            if start <= event_date <= end:
                candidates.append(
                    {
                        "kind": "holiday",
                        "title": item["title"],
                        "at": item["date"],
                        "source": item["source"],
                    }
                )
        elif item.get("start"):
            event_date = datetime.fromisoformat(item["start"]).date()
            if start <= event_date <= end:
                candidates.append(
                    {
                        "kind": "event",
                        "title": item["title"],
                        "at": item["start"],
                        "source": item["provider"],
                    }
                )
    from memory.local_store import list_reminders

    for reminder in list_reminders(
        owner_id, datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    ):
        due = datetime.fromisoformat(str(reminder["due_at"]).replace("Z", "+00:00"))
        if due.date() <= end:
            candidates.append(
                {
                    "kind": "reminder",
                    "title": reminder["message"],
                    "at": due.isoformat(),
                    "source": "user reminder",
                }
            )
    unique = {
        (item["kind"], item["title"].casefold(), item["at"][:10]): item
        for item in candidates
    }
    return sorted(unique.values(), key=lambda item: item["at"])[:50]


def enrol_project(owner_id: str, path: str) -> dict:
    from agent.tooling.policies import action_root

    root = action_root(owner_id, {}, path).resolve()
    return save_personal_item(
        owner_id,
        "enrolled_project",
        {
            "id": f"project-{hashlib.sha256(str(root).encode()).hexdigest()[:16]}",
            "root": str(root),
            "enrolled_at": datetime.now(timezone.utc).isoformat(),
            "enabled": True,
        },
    )


def project_health(owner_id: str, project_id: str, max_files: int = 2000) -> dict:
    project = next(
        (
            item
            for item in list_personal_items(owner_id, "enrolled_project")
            if item["id"] == project_id and item.get("enabled")
        ),
        None,
    )
    if not project:
        raise PermissionError("Project is not explicitly enrolled")
    root = Path(project["root"])
    files = []
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            files.append(path)
            if len(files) >= max(1, min(max_files, 5000)):
                break
    manifests = [
        str(path.relative_to(root))
        for path in files
        if path.name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
    ]
    findings = []
    if not any(path.name.startswith("README") for path in files):
        findings.append(
            {
                "evidence": "No README file found",
                "risk": "low",
                "affected_files": [],
                "test_command": None,
            }
        )
    result = {
        "project_id": project_id,
        "files_inspected": len(files),
        "truncated": len(files) >= max_files,
        "manifests": manifests,
        "findings": findings,
        "read_only": True,
    }
    if findings:
        save_personal_item(
            owner_id,
            "project_finding",
            {
                "project_id": project_id,
                "findings": findings[:20],
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "reason": "Scheduled read-only health check of an explicitly enrolled project",
            },
        )
    return result


def schedule_project_health(
    owner_id: str, project_id: str, cadence_hours: int = 168
) -> dict:
    enrolled = {
        item["id"]
        for item in list_personal_items(owner_id, "enrolled_project")
        if item.get("enabled")
    }
    if project_id not in enrolled:
        raise PermissionError("Project is not explicitly enrolled")
    cadence = max(24, min(int(cadence_hours), 24 * 30))
    return save_personal_item(
        owner_id,
        "project_health_schedule",
        {
            "project_id": project_id,
            "cadence_hours": cadence,
            "read_only": True,
            "max_files": 2000,
            "max_seconds": 30,
            "enabled": True,
        },
    )


def preview_email(
    owner_id: str,
    recipient: str,
    subject: str,
    body: str,
    attachments: list[str] | None = None,
) -> dict:
    if _UNTRUSTED_INSTRUCTION.search(body):
        raise PermissionError(
            "Email content contains an untrusted action or credential instruction"
        )
    return save_personal_item(
        owner_id,
        "email_draft",
        {
            "recipient": recipient[:254],
            "subject": subject[:200],
            "body": body[:10000],
            "attachments": [Path(item).name for item in (attachments or [])[:10]],
            "status": "preview",
            "requires_permission": "gmail_send",
            "requires_fresh_approval": True,
        },
    )


def normalize_inbound_email(message: dict) -> dict:
    """Expose mail as untrusted data; embedded action requests never become authority."""
    body = str(message.get("body") or "")[:10000]
    attachments = [
        {
            "name": Path(str(item.get("name", "attachment"))).name,
            "size": max(0, int(item.get("size", 0))),
            "untrusted": True,
        }
        for item in message.get("attachments", [])[:20]
    ]
    return {
        "id": str(message.get("id", ""))[:160],
        "sender": str(message.get("sender", ""))[:254],
        "subject": str(message.get("subject", ""))[:200],
        "body": body,
        "attachments": attachments,
        "untrusted": True,
        "embedded_action_ignored": bool(_UNTRUSTED_INSTRUCTION.search(body)),
        "grants_permissions": False,
        "authorizes_tools": False,
    }


def preview_calendar_write(owner_id: str, title: str, start: str, end: str) -> dict:
    start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if end_at <= start_at:
        raise ValueError("Calendar event end must follow its start")
    return save_personal_item(
        owner_id,
        "calendar_write_preview",
        {
            "title": title[:160],
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "status": "preview",
            "requires_permission": "calendar_write",
            "requires_fresh_approval": True,
        },
    )


def preview_pull_request(
    owner_id: str, project_id: str, branch: str, title: str, diff: str, tests: str
) -> dict:
    if not branch.startswith("curie/"):
        raise PermissionError("Curie changes require a dedicated curie/* branch")
    if re.search(r"(?i)merge|branch protection|deploy|secret", title):
        raise PermissionError(
            "PR previews cannot merge, alter protection, deploy, or reveal secrets"
        )
    if not diff.strip() or not tests.strip():
        raise ValueError("A diff and test evidence are required before PR preview")
    enrolled = {
        item["id"]
        for item in list_personal_items(owner_id, "enrolled_project")
        if item.get("enabled")
    }
    if project_id not in enrolled:
        raise PermissionError("Project is not explicitly enrolled")
    return save_personal_item(
        owner_id,
        "pull_request_preview",
        {
            "project_id": project_id,
            "branch": branch[:160],
            "title": title[:200],
            "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "test_summary": tests[:1000],
            "status": "preview",
            "requires_permission": "external_code_host",
            "requires_fresh_approval": True,
            "merge_allowed": False,
        },
    )


def request_external_write(owner_id: str, item_id: str) -> str:
    """Create a single-use approval only after a complete stored preview."""
    item = next(
        (row for row in list_personal_items(owner_id) if row.get("id") == item_id), None
    )
    if (
        not item
        or item.get("status") != "preview"
        or not item.get("requires_fresh_approval")
    ):
        raise PermissionError("A complete owner-scoped preview is required")
    from memory.repositories import get_repositories

    return get_repositories().approvals.create(
        owner_id,
        {
            "action": "personal_external_write",
            "item_id": item_id,
            "permission": item["requires_permission"],
        },
    )


def execute_external_write(
    owner_id: str, item_id: str, approval_token: str, adapter
) -> Any:
    """Consume fresh approval and invoke an injected connector; never merge or deploy."""
    from memory.repositories import get_repositories

    approval = get_repositories().approvals.consume(owner_id, approval_token, True)
    if (
        not approval
        or approval.get("action") != "personal_external_write"
        or approval.get("item_id") != item_id
    ):
        raise PermissionError(
            "Approval is invalid, expired, consumed, or belongs to another user"
        )
    item = next(
        (row for row in list_personal_items(owner_id) if row.get("id") == item_id), None
    )
    if not item or item.get("status") != "preview":
        raise PermissionError("Preview no longer exists")
    if item.get("merge_allowed") is False and getattr(adapter, "operation", "") in {
        "merge",
        "deploy",
    }:
        raise PermissionError("Curie never merges or deploys autonomously")
    result = adapter(dict(item))
    item["status"] = "submitted"
    item["submitted_at"] = datetime.now(timezone.utc).isoformat()
    kind = next(
        (
            kind
            for kind in (
                "email_draft",
                "calendar_write_preview",
                "pull_request_preview",
                "account_preview",
            )
            if item_id in {row["id"] for row in list_personal_items(owner_id, kind)}
        ),
        "external_preview",
    )
    save_personal_item(owner_id, kind, item)
    get_repositories().audits.append(
        owner_id,
        f"submit_{kind}",
        "completed",
        {
            "connector": kind,
            "validated_action": f"submit_{kind}",
            "parameters": item,
            "policy_decision": "fresh_approval_consumed",
            "approval": {"required": True, "granted": True, "token": approval_token},
            "outcome": {"status": "submitted", "result": str(result)[:500]},
        },
    )
    return result


def preview_account_registration(owner_id: str, service: str, fields: dict) -> dict:
    rendered = json.dumps(fields, default=str)
    if _FORBIDDEN_ACCOUNT.search(rendered):
        raise PermissionError(
            "CAPTCHAs, terms acceptance, invented identity, and credential reuse are prohibited"
        )
    forbidden = {
        key
        for key in fields
        if re.search(r"(?i)password|captcha|accept.*terms", str(key))
    }
    if forbidden:
        raise PermissionError(
            "Registration preview cannot retain passwords, CAPTCHAs, or terms acceptance"
        )
    return save_personal_item(
        owner_id,
        "account_preview",
        {
            "service": service[:120],
            "fields": fields,
            "status": "preview",
            "requires_permission": "account_submit",
            "requires_fresh_approval": True,
        },
    )


def daily_briefing_candidate(
    owner_id: str, profile: dict, now: datetime | None = None
) -> dict | None:
    """Rank concrete sources and return one bounded, traceable briefing."""
    now = now or datetime.now(timezone.utc)
    from services.proactive_policy import delivery_allowed

    allowed, _ = delivery_allowed(profile, "daily briefing", now)
    if not allowed:
        return None
    priorities = {"reminder": 0, "event": 1, "birthday": 2, "holiday": 3}
    items = sorted(
        agenda(owner_id, now.date(), 7),
        key=lambda item: (priorities.get(item["kind"], 9), item["at"]),
    )[:5]
    for finding in list_personal_items(owner_id, "project_finding")[-2:]:
        if len(items) >= 5:
            break
        findings = finding.get("findings") or []
        if findings:
            items.append(
                {
                    "kind": "project",
                    "title": str(findings[0].get("evidence", "Project health finding")),
                    "at": str(finding.get("checked_at", now.isoformat())),
                    "source": str(finding.get("reason")),
                }
            )
    if not items:
        return None
    lines = [
        f"{item['kind'].title()}: {item['title']} ({item['at'][:10]})" for item in items
    ]
    sources = sorted({item["source"] for item in items})
    return {
        "message": "Your upcoming agenda:\n" + "\n".join(f"- {line}" for line in lines),
        "reason": f"Based on {len(items)} stored upcoming item(s) from {', '.join(sources)[:120]}.",
        "topic": "daily briefing",
        "count": len(items),
    }


def handle_personal_ops_command(owner_id: str, text: str) -> str | None:
    stripped = text.strip()
    if stripped.casefold() == "/agenda":
        items = agenda(owner_id)
        return (
            "No upcoming agenda items."
            if not items
            else "Upcoming agenda:\n"
            + "\n".join(
                f"- {item['at']}: {item['title']} [{item['kind']}]"
                for item in items[:20]
            )
        )
    match = re.fullmatch(
        r"/birthday add\s+(.+?)\s+(\d{1,2})-(\d{1,2})(?:-(\d{4}))?", stripped, re.I
    )
    if match:
        item = add_birthday(
            owner_id,
            match.group(1),
            int(match.group(2)),
            int(match.group(3)),
            year=int(match.group(4)) if match.group(4) else None,
        )
        return f"Stored private birthday `{item['id']}` from your explicit instruction."
    return None
