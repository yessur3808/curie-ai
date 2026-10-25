"""Owner-scoped, hourly public awareness. Refreshing never sends a message."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os
from urllib.parse import urlparse

from memory.local_store import list_personal_items, save_personal_item
from utils.temporal_context import local_now, parse_time

KIND = "current_awareness"
TTL = timedelta(hours=1)


def _state(owner_id):
    return next(iter(list_personal_items(str(owner_id), KIND)), {})


def location_for(profile):
    # An old travel destination is not the owner's current location.
    return str(profile.get("location") or os.getenv("DEFAULT_LOCATION") or "").strip()[
        :100
    ]


def _published(value):
    stamp = parse_time(value)
    if stamp:
        return stamp
    try:
        return parsedate_to_datetime(str(value)).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def current_headlines(items, now):
    from services.autonomous_learning import _source_quality

    selected, seen = [], set()
    for raw in items:
        stamp = _published(raw.get("published_at"))
        url = str(raw.get("url") or "")
        parsed = urlparse(url)
        if (
            not stamp
            or not now - timedelta(hours=24) <= stamp <= now + timedelta(minutes=5)
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or _source_quality(str(raw.get("domain") or "")) < 0.7
            or len(url) > 2048
            or url in seen
        ):
            continue
        seen.add(url)
        selected.append(
            {
                "headline": str(raw.get("headline") or raw.get("title") or "")[:240],
                "publisher": str(raw.get("publisher") or raw.get("domain") or "")[:100],
                "url": url,
                "published_at": stamp.isoformat(),
                "evidence_level": "publisher headline only; article details not verified",
            }
        )
    return sorted(selected, key=lambda item: item["published_at"], reverse=True)[:5]


class CurrentAwarenessService:
    def __init__(self, weather=None, news=None):
        from utils.weather import get_weather
        from services.autonomous_learning import fetch_google_news

        self.weather = weather or get_weather
        self.news = news or fetch_google_news

    async def refresh(self, owner_id, profile, *, now=None, force=False):
        now = now or datetime.now(timezone.utc)
        previous = _state(owner_id)
        location = location_for(profile)
        last_attempt = parse_time(previous.get("last_attempt_at"))
        interval = TTL if previous.get("status") == "ready" else timedelta(minutes=10)
        if (
            not force
            and previous.get("location") == location
            and previous.get("local_date") == local_now(profile, now).date().isoformat()
            and last_attempt
            and now - last_attempt < interval
        ):
            return previous

        async def fetch(call):
            try:
                return await asyncio.wait_for(call, timeout=25), None
            except Exception as exc:
                return None, type(exc).__name__

        # Public sources only. No conversation, profile dump or private calendar is sent.
        calls = [fetch(self.news("global top news"))]
        if location:
            calls.extend(
                [fetch(self.news(f"{location} when:1d")), fetch(self.weather(location))]
            )
        results = await asyncio.gather(*calls)
        news = [
            {**item, "scope": "global"}
            for item in current_headlines(results[0][0] or [], now)[:3]
        ]
        if location:
            regional = [
                {**item, "scope": "regional"}
                for item in current_headlines(results[1][0] or [], now)[:2]
            ]
            news = regional + [
                item for item in news if item["url"] not in {r["url"] for r in regional}
            ]
        weather = results[-1][0] if location else None
        if not isinstance(weather, dict):
            weather = None
        errors = [error for _, error in results if error]
        if weather:
            observed = parse_time(weather.get("observed_at"))
            if not observed or not now - timedelta(
                hours=2
            ) <= observed <= now + timedelta(minutes=5):
                weather = None
                errors.append("stale_or_undated_weather")
        if not news:
            errors.append("no_recent_headlines")
        # Never extend an old observation's lifetime after a failed refresh.
        document = {
            "id": previous.get("id") or "current-awareness-v1",
            "location": location,
            "local_date": local_now(profile, now).date().isoformat(),
            "fetched_at": now.isoformat(),
            "expires_at": (now + TTL).isoformat(),
            "last_attempt_at": now.isoformat(),
            "weather": weather,
            "news": news,
            "status": "ready" if not errors and (weather or news) else "degraded",
            "errors": errors,
        }
        return save_personal_item(str(owner_id), KIND, document)


def awareness_context(owner_id, profile, *, now=None):
    """Read a bounded snapshot without doing network work on the chat path."""
    if not profile.get("hourly_awareness_enabled", False):
        return None
    now = now or datetime.now(timezone.utc)
    item = _state(owner_id)
    expiry = parse_time(item.get("expires_at"))
    if (
        not expiry
        or expiry <= now
        or item.get("location") != location_for(profile)
        or item.get("local_date") != local_now(profile, now).date().isoformat()
    ):
        return {
            "status": "unavailable_or_stale",
            "instruction": "No current snapshot. Use live tools for current facts.",
        }
    return {
        key: item.get(key)
        for key in (
            "status",
            "location",
            "local_date",
            "fetched_at",
            "expires_at",
            "weather",
            "news",
        )
    }


async def news_brief(query, profile=None):
    """Fresh, attributed headlines, not invented full-article analysis."""
    from services.autonomous_learning import fetch_google_news

    now = datetime.now(timezone.utc)
    location = location_for(profile or {})
    query = str(query).strip()
    # A broad 'today's news' request includes home-region and global coverage.
    import re

    broad = bool(
        re.fullmatch(
            r"(?i)(?:(?:what(?:'s| is| are)|show me|tell me|check|any)\s+)?(?:the\s+)?(?:(?:today(?:'s)?|latest|current)\s+)?(?:news|headlines|news updates)[?.! ]*",
            query,
        )
    )
    queries = (
        (
            [f"{location} when:1d", "global top news"]
            if location
            else ["global top news"]
        )
        if broad
        else [query + " when:1d"]
    )
    results = await asyncio.gather(
        *(fetch_google_news(value) for value in queries), return_exceptions=True
    )
    items = current_headlines(
        [item for result in results if isinstance(result, list) for item in result], now
    )
    if not items:
        return None
    lines = ["Here are the latest reported headlines I could check:"]
    for item in items[:4]:
        lines.append(
            f"- {item['headline']} — {item['publisher']} ([source]({item['url']}), published {item['published_at'][:10]})."
        )
    lines.append(
        "These are publisher headlines, not a full article-by-article verification."
    )
    return {
        "answer": "\n".join(lines),
        "sources": [{**item, "fetched_at": now.isoformat()} for item in items[:4]],
        "evidence_level": "headlines_only",
    }
