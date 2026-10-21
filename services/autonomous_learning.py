"""Bounded, source-linked autonomous research for Curie.

This service is deliberately separate from Curie's personal memory.  It may
read public sources and maintain an owner-scoped research journal, but it does
not change model weights, edit code, or promote web claims into user facts.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, time, timedelta, timezone
import hashlib
import html
import logging
import os
import re
import time as monotonic_time
from typing import Any
from urllib.parse import quote_plus, urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from memory import UserManager
from memory.local_store import list_personal_items, save_personal_item

logger = logging.getLogger(__name__)

FINDING_KIND = "autonomous_research_finding"
STATE_KIND = "autonomous_research_state"
STATE_VERSION = 1
DEFAULT_NEWS_TIMES = ("08:30", "18:30")
DEFAULT_TOPIC_INTERVAL_HOURS = 24
DEFAULT_SHARE_MAX = 1
MIN_SHARE_CONFIDENCE = 0.72
MAX_JOURNAL_ITEMS = 200

_COMMAND = re.compile(
    r"^/?(?:autolearn|autonomous-learning)(?:\s+(?P<action>.*))?$", re.I
)
_TAG = re.compile(r"<[^>]+>")
_WORD = re.compile(r"[a-z0-9][a-z0-9'+-]{2,}", re.I)
_SPACE = re.compile(r"\s+")
_NEWS_SUFFIX = re.compile(r"\s+-\s+[^-]{2,60}$")
_UNSAFE_TOPIC = re.compile(
    r"\b(?:password|secret|credential|token|api key|bank account|private key|seed phrase)\b",
    re.I,
)
_TITLE_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "against",
        "amid",
        "among",
        "and",
        "are",
        "been",
        "before",
        "being",
        "but",
        "for",
        "from",
        "has",
        "have",
        "into",
        "its",
        "more",
        "new",
        "not",
        "over",
        "says",
        "that",
        "the",
        "their",
        "than",
        "this",
        "through",
        "under",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "would",
    }
)
_LOW_QUALITY_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "pinterest.com",
        "reddit.com",
        "tiktok.com",
        "x.com",
        "youtube.com",
    }
)
_HIGH_QUALITY_DOMAINS = frozenset(
    {
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "bloomberg.com",
        "economist.com",
        "ft.com",
        "gov.hk",
        "hkfp.com",
        "nature.com",
        "npr.org",
        "reuters.com",
        "rthk.hk",
        "scmp.com",
        "science.org",
        "theguardian.com",
        "who.int",
    }
)

Researcher = Callable[[str], Awaitable[dict[str, Any]]]
NewsFetcher = Callable[[str], Awaitable[list[dict[str, Any]]]]
Synthesizer = Callable[[str, list[str]], Awaitable[str]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _timezone(profile: dict) -> ZoneInfo:
    try:
        return ZoneInfo(str(profile.get("timezone") or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _clean_text(value: Any, limit: int = 500) -> str:
    text = html.unescape(_TAG.sub(" ", str(value or "")))
    return _SPACE.sub(" ", text).strip()[:limit]


def _clean_summary(value: Any, limit: int = 700) -> str:
    text = str(value or "").strip()
    text = text.replace("Synthesis (model inference unless source-cited):", "").strip()
    text = text.split("\n\nEvidence passages:", 1)[0].strip()
    text = re.sub(r"\[unsupported citation removed\]", "", text, flags=re.I)
    text = _SPACE.sub(" ", text)
    return text[:limit].rstrip(" ,;:")


def _domain(url: str) -> str:
    host = (urlparse(str(url)).hostname or "").casefold().removeprefix("www.")
    return host


def _source_quality(domain: str) -> float:
    domain = str(domain).casefold().removeprefix("www.")
    if not domain or any(
        domain == bad or domain.endswith(f".{bad}") for bad in _LOW_QUALITY_DOMAINS
    ):
        return 0.2
    if any(
        domain == good or domain.endswith(f".{good}") for good in _HIGH_QUALITY_DOMAINS
    ):
        return 0.95
    if domain.endswith((".gov", ".gov.hk", ".edu", ".ac.uk")):
        return 0.95
    if domain.endswith((".org", ".int")):
        return 0.72
    return 0.62


def _distinct_reliable_sources(sources: Iterable[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for raw in sources:
        item = dict(raw)
        domain = str(item.get("domain") or _domain(str(item.get("url") or "")))
        quality = float(item.get("quality") or _source_quality(domain))
        if not domain or quality < 0.55:
            continue
        item["domain"] = domain
        item["quality"] = round(quality, 2)
        selected.setdefault(domain, item)
    return list(selected.values())


def confidence_for(sources: Iterable[dict], summary: str = "") -> float:
    reliable = _distinct_reliable_sources(sources)
    count = len(reliable)
    if count == 0:
        return 0.25
    if count == 1:
        return 0.55
    confidence = 0.52 + min(count, 4) * 0.10
    confidence += min(
        sum(float(item["quality"]) for item in reliable) / count - 0.6, 0.08
    )
    if len(re.findall(r"\[S\d+\]", summary)) >= 2:
        confidence += 0.04
    if re.search(
        r"\b(?:conflict|disagree|discrepanc|uncertain|unconfirmed)\b", summary, re.I
    ):
        confidence -= 0.12
    return round(max(0.0, min(confidence, 0.95)), 2)


def _flatten_profile_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values = []
        for key in ("name", "title", "topic", "destination", "location"):
            if value.get(key):
                values.append(str(value[key]))
        return values
    if isinstance(value, (list, tuple, set)):
        values = []
        for item in value:
            values.extend(_flatten_profile_values(item))
        return values
    return []


def derive_research_topics(profile: dict) -> list[str]:
    """Return a small, non-sensitive set of explicitly relevant topics."""
    raw: list[str] = []
    for key in ("autonomous_research_topics", "interests", "projects"):
        raw.extend(_flatten_profile_values(profile.get(key)))
    excluded = {
        str(item).casefold() for item in profile.get("proactive_avoid_topics", [])
    }
    topics: list[str] = []
    seen: set[str] = set()
    for value in raw:
        topic = _clean_text(value, 100).strip(" .,-")
        folded = topic.casefold()
        if (
            len(topic) < 2
            or folded in seen
            or _UNSAFE_TOPIC.search(topic)
            or any(blocked and blocked in folded for blocked in excluded)
        ):
            continue
        seen.add(folded)
        topics.append(topic)
        if len(topics) >= 10:
            break
    return topics


def derive_relevant_areas(profile: dict) -> list[str]:
    raw: list[str] = []
    for key in ("location", "country", "region", "upcoming_trip_destination"):
        raw.extend(_flatten_profile_values(profile.get(key)))
    areas: list[str] = []
    seen: set[str] = set()
    for value in raw:
        area = _clean_text(value, 100).strip(" .,-")
        folded = area.casefold()
        if len(area) >= 2 and folded not in seen and not _UNSAFE_TOPIC.search(area):
            seen.add(folded)
            areas.append(area)
    return areas[:5]


def _parse_clock(value: str) -> time | None:
    try:
        hour, minute = [int(part) for part in str(value).split(":", 1)]
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError):
        return None


def due_news_slot(profile: dict, state: dict, now: datetime) -> str | None:
    local_now = now.astimezone(_timezone(profile))
    values = profile.get("autonomous_news_times") or DEFAULT_NEWS_TIMES
    completed = set(state.get("completed_news_slots") or [])
    attempts = dict(state.get("news_slot_attempts") or {})
    for value in sorted({str(item) for item in values}):
        clock = _parse_clock(value)
        if clock is None:
            continue
        slot = f"{local_now.date().isoformat()}T{value}"
        if slot in completed or local_now.time() < clock:
            continue
        last_attempt = _parse_datetime(attempts.get(slot))
        if last_attempt and now - last_attempt < timedelta(hours=2):
            continue
        return slot
    return None


def _state(owner_id: str) -> dict:
    rows = list_personal_items(owner_id, STATE_KIND)
    if rows:
        return dict(rows[-1])
    return {
        "id": "autonomous-research-state-v1",
        "version": STATE_VERSION,
        "completed_news_slots": [],
        "news_slot_attempts": {},
        "topic_cursor": 0,
        "share_count": 0,
        "share_date": "",
    }


def _save_state(owner_id: str, state: dict) -> dict:
    item = dict(state)
    item["version"] = STATE_VERSION
    item["updated_at"] = _utc_now().isoformat()
    return save_personal_item(owner_id, STATE_KIND, item)


def list_findings(owner_id: str, *, include_inactive: bool = True) -> list[dict]:
    findings = [dict(row) for row in list_personal_items(owner_id, FINDING_KIND)]
    if not include_inactive:
        findings = [row for row in findings if row.get("status", "active") == "active"]
    return sorted(
        findings,
        key=lambda item: _parse_datetime(item.get("observed_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _fingerprint(topic: str, summary: str, sources: Iterable[dict]) -> str:
    source_domains = sorted(
        {str(item.get("domain") or _domain(item.get("url", ""))) for item in sources}
    )
    material = "|".join(
        [topic.casefold(), _clean_text(summary, 400).casefold(), *source_domains]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _novelty(owner_id: str, topic: str, summary: str) -> float:
    current = set(_WORD.findall(f"{topic} {summary}".casefold())) - _TITLE_STOPWORDS
    if not current:
        return 0.0
    best = 0.0
    for existing in list_findings(owner_id)[:40]:
        prior = (
            set(
                _WORD.findall(
                    f"{existing.get('topic', '')} {existing.get('summary', '')}".casefold()
                )
            )
            - _TITLE_STOPWORDS
        )
        if prior:
            best = max(best, len(current & prior) / len(current | prior))
    return round(max(0.0, 1.0 - best), 2)


def save_finding(owner_id: str, finding: dict, now: datetime | None = None) -> dict:
    now = now or _utc_now()
    item = dict(finding)
    item["topic"] = _clean_text(item.get("topic"), 160)
    item["summary"] = _clean_summary(item.get("summary"), 1200)
    item["sources"] = [dict(source) for source in item.get("sources", [])][:8]
    reliable = _distinct_reliable_sources(item["sources"])
    item["source_count"] = len(item["sources"])
    item["independent_source_count"] = len(reliable)
    item["confidence"] = confidence_for(item["sources"], item["summary"])
    item.setdefault("observed_at", now.isoformat())
    item["last_verified_at"] = now.isoformat()
    item.setdefault("expires_at", (now + timedelta(days=14)).isoformat())
    item.setdefault("status", "active")
    item.setdefault("query", item["topic"])
    item.setdefault("kind", "topic_research")
    item.setdefault(
        "agreement", f"Compared {len(reliable)} independent reliable sources."
    )
    item.setdefault("usefulness", 0.7)
    item["novelty"] = _novelty(owner_id, item["topic"], item["summary"])
    item["fingerprint"] = _fingerprint(item["topic"], item["summary"], item["sources"])

    existing_rows = list_findings(owner_id)
    exact = next(
        (row for row in existing_rows if row.get("fingerprint") == item["fingerprint"]),
        None,
    )
    if exact:
        item = {
            **exact,
            **item,
            "verification_count": int(exact.get("verification_count", 1)) + 1,
        }
    else:
        item["id"] = f"finding:{item['fingerprint']}"
        item["verification_count"] = 1
        for prior in existing_rows:
            if prior.get("status", "active") not in {"active", "stale"}:
                continue
            if str(prior.get("topic", "")).casefold() != item["topic"].casefold():
                continue
            prior["status"] = "superseded"
            prior["superseded_at"] = now.isoformat()
            prior["superseded_by_fingerprint"] = item["fingerprint"]
            save_personal_item(owner_id, FINDING_KIND, prior)
    return save_personal_item(owner_id, FINDING_KIND, item)


async def _default_researcher(query: str) -> dict[str, Any]:
    items = await fetch_google_news(query)
    if not items:
        return {"answer": "", "sources": []}
    clusters = cluster_news(items)
    cluster = next(
        (
            candidate
            for candidate in clusters
            if len(_distinct_reliable_sources(candidate)) >= 2
        ),
        clusters[0],
    )
    reliable = _distinct_reliable_sources(cluster)
    selected = reliable[:3] if reliable else cluster[:3]
    snippets = [
        (
            f"Headline: {item.get('headline') or item.get('title')}\n"
            f"Publisher: {item.get('publisher')}\n"
            f"Published: {item.get('published_at')}\n"
            f"Summary: {item.get('summary')}\nURL: {item.get('url')}"
        )
        for item in selected
    ]
    answer = await _default_synthesizer(query, snippets)
    return {
        "answer": answer,
        "sources": [
            {
                "id": f"S{index}",
                "url": str(item.get("url") or ""),
                "passage": _clean_text(item.get("summary"), 400),
                "fetched_at": _utc_now().isoformat(),
                "publisher": str(item.get("publisher") or ""),
                "publisher_domain": str(item.get("domain") or ""),
            }
            for index, item in enumerate(selected, 1)
        ],
    }


async def _default_synthesizer(query: str, snippets: list[str]) -> str:
    """Produce a fast, auditable comparison without competing with live chat."""
    del query
    headlines: list[str] = []
    publishers: list[str] = []
    for snippet in snippets:
        headline = re.search(r"^Headline:\s*(.+)$", snippet, re.M)
        publisher = re.search(r"^Publisher:\s*(.+)$", snippet, re.M)
        if headline:
            headlines.append(_clean_text(headline.group(1), 260))
        if publisher:
            publishers.append(_clean_text(publisher.group(1), 80))
    if not headlines:
        return ""
    lead = min(headlines, key=len).rstrip(" .")
    citations = " ".join(f"[S{index}]" for index in range(1, len(headlines) + 1))
    names = ", ".join(dict.fromkeys(publishers))
    if len(set(publishers)) >= 2:
        return (
            f"Multiple independent outlets are reporting the same core development: "
            f"{lead}. {citations} The corroborating publishers are {names}; details "
            "unique to one report remain provisional."
        )
    return (
        f"One reliable outlet is reporting: {lead}. [S1] This remains a single-source "
        "finding and should not be treated as corroborated."
    )


async def fetch_google_news(query: str) -> list[dict[str, Any]]:
    """Fetch a bounded public Google News RSS result set (no account or API key)."""
    if query.strip() == "global top news":
        url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    else:
        url = (
            "https://news.google.com/rss/search?q="
            f"{quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        )
    timeout = float(os.getenv("AUTONOMOUS_RESEARCH_HTTP_TIMEOUT", "15"))
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": "CurieResearch/1.0 (+source-comparison)"},
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ValueError("news feed exceeded the one-megabyte limit")
    root = ET.fromstring(response.content)
    items: list[dict[str, Any]] = []
    for node in root.findall("./channel/item")[:40]:
        source = node.find("source")
        title = _clean_text(node.findtext("title"), 240)
        publisher = _clean_text(source.text if source is not None else "", 100)
        publisher_url = source.attrib.get("url", "") if source is not None else ""
        link = _clean_text(node.findtext("link"), 600)
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "headline": _NEWS_SUFFIX.sub("", title),
                "summary": _clean_text(node.findtext("description"), 600),
                "url": link,
                "publisher": publisher or _domain(publisher_url) or "News source",
                "domain": _domain(publisher_url) or _domain(link),
                "published_at": _clean_text(node.findtext("pubDate"), 80),
            }
        )
    return items


def _title_terms(title: str) -> set[str]:
    return set(_WORD.findall(title.casefold())) - _TITLE_STOPWORDS


def _title_similarity(left: str, right: str) -> float:
    a, b = _title_terms(left), _title_terms(right)
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    return max(overlap / len(a | b), overlap / min(len(a), len(b)) * 0.75)


def cluster_news(items: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for item in items:
        headline = str(item.get("headline") or item.get("title") or "")
        target = next(
            (
                cluster
                for cluster in clusters
                if max(
                    _title_similarity(
                        headline,
                        str(member.get("headline") or member.get("title") or ""),
                    )
                    for member in cluster
                )
                >= 0.34
            ),
            None,
        )
        if target is None:
            clusters.append([item])
        else:
            target.append(item)
    return sorted(
        clusters,
        key=lambda cluster: (
            len({str(item.get("domain") or item.get("publisher")) for item in cluster}),
            len(cluster),
        ),
        reverse=True,
    )


class AutonomousLearningService:
    """Periodically researches, verifies, refreshes, and curates findings."""

    def __init__(
        self,
        *,
        researcher: Researcher | None = None,
        news_fetcher: NewsFetcher | None = None,
        synthesizer: Synthesizer | None = None,
    ):
        self.researcher = researcher or _default_researcher
        self.news_fetcher = news_fetcher or fetch_google_news
        self.synthesizer = synthesizer or _default_synthesizer
        self._active_owners: set[str] = set()

    async def run_due(
        self, owner_id: str, profile: dict | None = None, now: datetime | None = None
    ) -> list[dict]:
        profile = dict(profile or UserManager.get_user_profile(owner_id) or {})
        if not profile.get("autonomous_learning_enabled", False):
            return []
        owner_id = str(owner_id)
        if owner_id in self._active_owners:
            return []
        from services.resource_governor import resource_governor
        from services.self_diagnostics import diagnostic_monitor

        decision = resource_governor.admit(
            "autonomous_learning",
            owner_id,
            2000,
            diagnostics=diagnostic_monitor.snapshot(owner_id=owner_id, limit=50),
        )
        if not decision.allowed:
            logger.info(
                "Autonomous learning paused by resource governor: %s", decision.reason
            )
            return []
        self._active_owners.add(owner_id)
        started = monotonic_time.perf_counter()
        try:
            result = await self._run_due_locked(owner_id, profile, now or _utc_now())
            resource_governor.record(
                "autonomous_learning",
                owner_id,
                tokens=0,
                wall_seconds=monotonic_time.perf_counter() - started,
                outcome="completed",
            )
            return result
        finally:
            self._active_owners.discard(owner_id)

    async def _run_due_locked(
        self, owner_id: str, profile: dict, now: datetime
    ) -> list[dict]:
        state = _state(owner_id)
        created: list[dict] = []

        stale = next(
            (
                item
                for item in list_findings(owner_id, include_inactive=False)
                if (_parse_datetime(item.get("expires_at")) or now) <= now
                and (
                    not _parse_datetime(item.get("last_refresh_attempt_at"))
                    or now - _parse_datetime(item.get("last_refresh_attempt_at"))
                    >= timedelta(hours=6)
                )
            ),
            None,
        )
        if stale:
            refreshed = await self._refresh_finding(owner_id, stale, now)
            if refreshed:
                created.append(refreshed)

        force_refresh = bool(state.pop("force_refresh", False))
        slot = due_news_slot(profile, state, now)
        if profile.get("autonomous_news_enabled", True) and (slot or force_refresh):
            slot = (
                slot
                or f"{now.astimezone(_timezone(profile)).date().isoformat()}Tmanual"
            )
            attempts = dict(state.get("news_slot_attempts") or {})
            attempts[slot] = now.isoformat()
            state["news_slot_attempts"] = dict(list(attempts.items())[-12:])
            news = await self._research_news_cycle(owner_id, profile, now)
            created.extend(news)
            if news:
                completed = [*state.get("completed_news_slots", []), slot]
                state["completed_news_slots"] = list(dict.fromkeys(completed))[-14:]

        last_topic = _parse_datetime(state.get("last_topic_research_at"))
        interval = max(
            6,
            min(
                int(
                    profile.get(
                        "autonomous_research_interval_hours",
                        DEFAULT_TOPIC_INTERVAL_HOURS,
                    )
                ),
                168,
            ),
        )
        if (
            profile.get("autonomous_topic_research_enabled", True)
            and derive_research_topics(profile)
            and (
                force_refresh
                or not last_topic
                or now - last_topic >= timedelta(hours=interval)
            )
        ):
            finding = await self._research_next_topic(owner_id, profile, state, now)
            state["last_topic_research_at"] = now.isoformat()
            if finding:
                created.append(finding)

        state["last_run_at"] = now.isoformat()
        state["last_run_created"] = len(created)
        _save_state(owner_id, state)
        return created

    async def _research_news_cycle(
        self, owner_id: str, profile: dict, now: datetime
    ) -> list[dict]:
        areas = derive_relevant_areas(profile)
        interests = derive_research_topics(profile)[:4]
        query_parts = []
        if areas:
            query_parts.append(" OR ".join(f'"{area}"' for area in areas))
        if interests:
            query_parts.append(" OR ".join(interests))
        regional_query = (
            " ".join(query_parts).strip() or "important regional developments"
        )
        queries = [
            ("regional news", f"{regional_query} when:1d"),
            ("global news", "global top news"),
        ]
        findings: list[dict] = []
        for topic, query in queries:
            try:
                result = await self._research_news_query(owner_id, topic, query, now)
            except Exception as exc:
                logger.warning("Autonomous news research failed for %s: %s", topic, exc)
                continue
            if result:
                findings.append(result)
        return findings

    async def _research_news_query(
        self, owner_id: str, topic: str, query: str, now: datetime
    ) -> dict | None:
        items = await self.news_fetcher(query)
        if not items:
            return None
        clusters = cluster_news(items)
        cluster = next(
            (
                candidate
                for candidate in clusters
                if len(_distinct_reliable_sources(candidate)) >= 2
            ),
            clusters[0],
        )
        reliable = _distinct_reliable_sources(cluster)
        selected = reliable[:3] if reliable else cluster[:3]
        snippets = [
            (
                f"Headline: {item.get('headline') or item.get('title')}\n"
                f"Publisher: {item.get('publisher')}\n"
                f"Published: {item.get('published_at')}\n"
                f"Summary: {item.get('summary')}\nURL: {item.get('url')}"
            )
            for item in selected
        ]
        request = (
            f"Identify what these reports agree on about this {topic} development. "
            "State only supported facts, cite every factual sentence, and explicitly note conflicts."
        )
        summary = _clean_summary(await self.synthesizer(request, snippets), 900)
        if not summary:
            summary = _clean_text(
                selected[0].get("headline") or selected[0].get("title"), 500
            )
        sources = []
        for index, item in enumerate(selected, 1):
            sources.append(
                {
                    "id": f"S{index}",
                    "url": str(item.get("url") or ""),
                    "publisher": str(
                        item.get("publisher") or item.get("domain") or "Source"
                    ),
                    "domain": str(item.get("domain") or _domain(item.get("url", ""))),
                    "title": _clean_text(
                        item.get("headline") or item.get("title"), 240
                    ),
                    "published_at": str(item.get("published_at") or ""),
                    "fetched_at": now.isoformat(),
                    "quality": _source_quality(str(item.get("domain") or "")),
                }
            )
        agreement = (
            f"Corroborated by {len(_distinct_reliable_sources(sources))} independent sources."
            if len(_distinct_reliable_sources(sources)) >= 2
            else "Only one reliable independent source was available; do not proactively share."
        )
        return save_finding(
            owner_id,
            {
                "kind": "news",
                "topic": topic,
                "summary": summary,
                "query": query,
                "sources": sources,
                "agreement": agreement,
                "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=36)).isoformat(),
                "usefulness": 0.86 if topic == "regional news" else 0.78,
            },
            now,
        )

    async def _research_next_topic(
        self, owner_id: str, profile: dict, state: dict, now: datetime
    ) -> dict | None:
        topics = derive_research_topics(profile)
        if not topics:
            return None
        cursor = int(state.get("topic_cursor", 0)) % len(topics)
        topic = topics[cursor]
        state["topic_cursor"] = (cursor + 1) % len(topics)
        query = f'"{topic}" latest research development when:7d'
        return await self._research_topic(owner_id, topic, query, now)

    async def _research_topic(
        self, owner_id: str, topic: str, query: str, now: datetime
    ) -> dict | None:
        result = await self.researcher(query)
        summary = _clean_summary(result.get("answer"), 900)
        sources = []
        for raw in result.get("sources") or []:
            url = str(raw.get("url") or "")
            domain = str(raw.get("publisher_domain") or _domain(url))
            if not url or not domain:
                continue
            sources.append(
                {
                    "id": str(raw.get("id") or f"S{len(sources) + 1}"),
                    "url": url,
                    "domain": domain,
                    "publisher": str(raw.get("publisher") or domain),
                    "passage": _clean_text(raw.get("passage"), 400),
                    "fetched_at": str(raw.get("fetched_at") or now.isoformat()),
                    "quality": _source_quality(domain),
                }
            )
        if not summary or not sources:
            return None
        reliable_count = len(_distinct_reliable_sources(sources))
        return save_finding(
            owner_id,
            {
                "kind": "topic_research",
                "topic": topic,
                "summary": summary,
                "query": query,
                "sources": sources,
                "agreement": (
                    f"Compared {reliable_count} independent reliable sources."
                    if reliable_count >= 2
                    else "Single-source result; retained with low confidence and blocked from sharing."
                ),
                "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(days=14)).isoformat(),
                "usefulness": 0.74,
            },
            now,
        )

    async def _refresh_finding(
        self, owner_id: str, stale: dict, now: datetime
    ) -> dict | None:
        stale["status"] = "stale"
        stale["last_refresh_attempt_at"] = now.isoformat()
        save_personal_item(owner_id, FINDING_KIND, stale)
        try:
            if stale.get("kind") == "news":
                return await self._research_news_query(
                    owner_id,
                    str(stale.get("topic") or "news"),
                    str(stale.get("query") or "global top news"),
                    now,
                )
            return await self._research_topic(
                owner_id,
                str(stale.get("topic") or "research topic"),
                str(stale.get("query") or stale.get("topic") or ""),
                now,
            )
        except Exception as exc:
            logger.warning(
                "Could not refresh stale finding %s: %s", stale.get("id"), exc
            )
            return None

    def share_candidate(
        self, owner_id: str, profile: dict | None = None, now: datetime | None = None
    ) -> dict | None:
        profile = dict(profile or UserManager.get_user_profile(owner_id) or {})
        if not (
            profile.get("autonomous_learning_enabled", False)
            and profile.get("autonomous_learning_share_enabled", False)
        ):
            return None
        now = now or _utc_now()
        local_date = now.astimezone(_timezone(profile)).date().isoformat()
        state = _state(owner_id)
        count = (
            int(state.get("share_count", 0))
            if state.get("share_date") == local_date
            else 0
        )
        daily_max = max(
            0,
            min(
                int(
                    profile.get(
                        "autonomous_learning_daily_share_max", DEFAULT_SHARE_MAX
                    )
                ),
                3,
            ),
        )
        if count >= daily_max:
            return None
        minimum = max(
            0.65,
            min(
                float(
                    profile.get(
                        "autonomous_learning_min_confidence", MIN_SHARE_CONFIDENCE
                    )
                ),
                0.95,
            ),
        )
        excluded = {
            str(item).casefold() for item in profile.get("proactive_avoid_topics", [])
        }
        eligible = []
        for item in list_findings(owner_id, include_inactive=False):
            expires = _parse_datetime(item.get("expires_at"))
            topic = str(item.get("topic") or "")
            if (
                item.get("shared_at")
                or not expires
                or expires <= now
                or float(item.get("confidence", 0)) < minimum
                or int(item.get("independent_source_count", 0)) < 2
                or float(item.get("novelty", 0)) < 0.35
                or any(value and value in topic.casefold() for value in excluded)
            ):
                continue
            eligible.append(item)
        if not eligible:
            return None
        finding = max(
            eligible,
            key=lambda item: (
                float(item.get("usefulness", 0)) * 0.45
                + float(item.get("confidence", 0)) * 0.35
                + float(item.get("novelty", 0)) * 0.20
            ),
        )
        sources = _distinct_reliable_sources(finding.get("sources", []))[:3]
        source_lines = []
        for source in sources:
            label = str(source.get("publisher") or source.get("domain") or "Source")
            source_lines.append(f"• {label}: {source.get('url')}")
        message = (
            f"I found something worth sharing, monsieur: {_clean_summary(finding.get('summary'), 560)}\n\n"
            f"Confidence: {round(float(finding.get('confidence', 0)) * 100)}% "
            f"from {len(sources)} independent sources.\n\nSources:\n"
            + "\n".join(source_lines)
        )
        return {
            "finding_id": finding["id"],
            "message": message[:1000],
            "reason": "A fresh, relevant finding passed Curie's multi-source confidence and usefulness checks.",
            "topic": f"discovery: {finding.get('topic', 'research')}",
            "kind": "tracked_goal",
            "confidence": float(finding.get("confidence", 0)),
            "urgency": 0.2,
            "usefulness": float(finding.get("usefulness", 0.75)),
            "priority": 0.82,
            "evidence": [
                {
                    "source": str(source.get("publisher") or source.get("domain")),
                    "reference": str(source.get("url")),
                    "confidence": float(source.get("quality", 0.65)),
                    "observed_at": str(
                        source.get("fetched_at") or finding.get("observed_at")
                    ),
                }
                for source in sources
            ],
        }

    def mark_shared(
        self, owner_id: str, finding_id: str, now: datetime | None = None
    ) -> None:
        now = now or _utc_now()
        finding = next(
            (row for row in list_findings(owner_id) if row.get("id") == finding_id),
            None,
        )
        if not finding:
            return
        finding["shared_at"] = now.isoformat()
        finding["share_count"] = int(finding.get("share_count", 0)) + 1
        save_personal_item(owner_id, FINDING_KIND, finding)
        profile = UserManager.get_user_profile(owner_id) or {}
        local_date = now.astimezone(_timezone(profile)).date().isoformat()
        state = _state(owner_id)
        count = (
            int(state.get("share_count", 0))
            if state.get("share_date") == local_date
            else 0
        )
        state["share_date"] = local_date
        state["share_count"] = count + 1
        state["last_shared_at"] = now.isoformat()
        _save_state(owner_id, state)


def request_refresh(owner_id: str) -> None:
    state = _state(owner_id)
    state["force_refresh"] = True
    _save_state(owner_id, state)


def handle_autonomous_learning_command(owner_id: str, text: str) -> str | None:
    match = _COMMAND.match(text.strip())
    if not match:
        return None
    profile = UserManager.get_user_profile(owner_id) or {}
    action = (match.group("action") or "status").strip()
    folded = action.casefold()
    if folded in {"enable", "on"}:
        UserManager.update_user_profile(
            owner_id,
            {
                "autonomous_learning_enabled": True,
                "autonomous_news_enabled": True,
                "autonomous_topic_research_enabled": True,
                "autonomous_learning_share_enabled": True,
                "autonomous_news_times": list(DEFAULT_NEWS_TIMES),
                "autonomous_learning_daily_share_max": DEFAULT_SHARE_MAX,
            },
        )
        request_refresh(owner_id)
        return "Autonomous research is on. I’ll check relevant and global news twice daily, rotate through your research topics, refresh stale findings, and share at most one worthwhile discovery per day."
    if folded in {"disable", "off"}:
        UserManager.update_user_profile(
            owner_id, {"autonomous_learning_enabled": False}
        )
        return "Autonomous research is off. Your existing source-linked journal is preserved."
    if folded in {"share on", "sharing on"}:
        UserManager.update_user_profile(
            owner_id, {"autonomous_learning_share_enabled": True}
        )
        return "I’ll occasionally share corroborated, useful discoveries within your normal proactive-message limits."
    if folded in {"share off", "sharing off"}:
        UserManager.update_user_profile(
            owner_id, {"autonomous_learning_share_enabled": False}
        )
        return "I’ll keep researching, but I won’t proactively share discoveries."
    if folded in {"refresh", "research now", "check now"}:
        request_refresh(owner_id)
        return "Queued. I’ll run a fresh news and topic-research pass on the next background cycle."
    if folded.startswith("topic add "):
        topic = _clean_text(action[10:], 100).strip(" .,-")
        if len(topic) < 2 or _UNSAFE_TOPIC.search(topic):
            return "That is not a safe or useful public research topic."
        topics = list(profile.get("autonomous_research_topics") or [])
        topics = list(dict.fromkeys([*topics, topic]))[-20:]
        UserManager.update_user_profile(
            owner_id, {"autonomous_research_topics": topics}
        )
        return f"Added `{topic}` to my research rotation."
    if folded.startswith("topic remove "):
        topic = _clean_text(action[13:], 100).casefold()
        topics = [
            item
            for item in profile.get("autonomous_research_topics", [])
            if str(item).casefold() != topic
        ]
        UserManager.update_user_profile(
            owner_id, {"autonomous_research_topics": topics}
        )
        return f"Removed `{topic}` from my research rotation."
    if folded in {"journal", "findings"}:
        rows = list_findings(owner_id, include_inactive=False)[:5]
        if not rows:
            return "The autonomous research journal is empty so far."
        lines = ["Recent research findings:"]
        for row in rows:
            expiry = _parse_datetime(row.get("expires_at"))
            lines.append(
                f"• {row.get('topic', 'Research')} — {round(float(row.get('confidence', 0)) * 100)}% confidence, "
                f"{row.get('independent_source_count', 0)} sources, expires {expiry.date().isoformat() if expiry else 'unknown'}"
            )
        return "\n".join(lines)
    if folded in {"status", "settings", "topics"}:
        topics = derive_research_topics(profile)
        times = profile.get("autonomous_news_times") or DEFAULT_NEWS_TIMES
        return (
            f"Autonomous research: {'on' if profile.get('autonomous_learning_enabled') else 'off'}\n"
            f"• News checks: {', '.join(str(value) for value in times)} ({profile.get('timezone') or 'UTC'})\n"
            f"• Relevant areas: {', '.join(derive_relevant_areas(profile)) or 'none learned yet'}\n"
            f"• Topic rotation: {', '.join(topics) or 'none yet'}\n"
            f"• Sharing: {'on' if profile.get('autonomous_learning_share_enabled') else 'off'}, "
            f"max {profile.get('autonomous_learning_daily_share_max', DEFAULT_SHARE_MAX)}/day\n"
            f"• Active findings: {len(list_findings(owner_id, include_inactive=False))}"
        )
    return "Use /autolearn status, enable, disable, refresh, journal, share on|off, topic add <topic>, or topic remove <topic>."


__all__ = [
    "AutonomousLearningService",
    "cluster_news",
    "confidence_for",
    "derive_relevant_areas",
    "derive_research_topics",
    "due_news_slot",
    "handle_autonomous_learning_command",
    "list_findings",
    "request_refresh",
    "save_finding",
]
