"""Fresh application time and conservative boundaries for recalled events."""

from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_time(value):
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def local_now(profile=None, now=None):
    profile = profile or {}
    name = profile.get("timezone") or os.getenv("DEFAULT_TIMEZONE") or "UTC"
    try:
        tz = ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    return (now or datetime.now(timezone.utc)).astimezone(tz)


def temporal_guidance(profile=None, now=None):
    current = local_now(profile, now)
    return (
        f"[AUTHORITATIVE CURRENT TIME]\nToday is {current:%A, %Y-%m-%d}. "
        f"The current local time is {current.isoformat(timespec='minutes')}. "
        "This application clock outranks dates implied by old conversation or memory. "
        "Compare recalled event dates with today before calling them upcoming or offering preparation. "
        "Do not silently move a past trip into next year. Undated memories and old assistant "
        "claims do not establish present circumstances or current weather/news. "
        "A date correction needs one brief acknowledgment, not repeated apologies or an invented explanation. "
        "Answer the current question and do not revive a past trip unless relevant."
    )


def recent_rows(rows, *, now=None, hours=48):
    """Proactive topic selection requires recency, not just a recent row index."""
    now = now or datetime.now(timezone.utc)
    result = []
    for row in rows:
        stamp = parse_time(row.get("created_at") or row.get("timestamp"))
        if stamp and now - timedelta(hours=hours) <= stamp <= now + timedelta(
            minutes=5
        ):
            result.append(row)
    return result
