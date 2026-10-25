"""Opt-in browser research, fenced by the Rust scheduler and per-slot receipts.

No paid X API, public writes, tools selected by page text, or model inference.
News-headline correlation is explicitly not advertised as claim verification.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import random
import re
import uuid
from urllib.parse import urlparse

from agent import task_engine
from connectors.x_browser import BrowserAccessError, collect_posts, session_metadata
from memory import UserManager
from memory.local_store import (
    delete_personal_item,
    finish_mutation_attempt,
    list_personal_items,
    reserve_mutation_attempt,
    save_personal_item,
)
from services.autonomous_learning import (
    _clean_text,
    _source_quality,
    _title_similarity,
    _timezone,
    fetch_google_news,
)
from utils.temporal_context import parse_time

KIND = "x_browser_research"
STATE_KIND = "x_research_state"
FINDING_KIND = "x_research_finding"
TOPICS = (
    ("AI news", '("AI model" OR "open source AI" OR LLM)'),
    ("AI setups and technology", '("AI setup" OR "local LLM" OR GPU OR technology)'),
    ("world news", '("world news" OR "international news")'),
    ("Hong Kong news", '("Hong Kong" OR 香港) news'),
    ("Egypt news", "(Egypt OR مصر) news"),
)
KEYWORDS = (
    r"\b(?:ai|llm|model|inference|agent|qwen|glm|dessa|artificial intelligence)\b",
    r"\b(?:gpu|setup|technology|hardware|software|chip|open.source|robot)\b",
    r"\b(?:world|international|election|government|ceasefire|war|climate|summit)\b",
    r"hong kong|香港|\bhk\b",
    r"\begypt|cairo|egyptian\b|مصر",
)


def state_for(owner):
    rows = list_personal_items(owner, STATE_KIND)
    return dict(rows[-1]) if rows else {"id": "x-research-state-v1"}


def daily_slots(owner, profile, now):
    """Stable per-day jitter survives restarts; 2–6 slots, 09:00–22:00 local."""
    day = now.astimezone(_timezone(profile)).date()
    rng = random.Random(  # nosec B311: schedule jitter, not cryptography
        hashlib.sha256(f"x-research:{owner}:{day}".encode()).hexdigest()
    )
    count = rng.randint(2, 6)
    start = datetime.combine(day, time(9), tzinfo=_timezone(profile))
    width = 13 * 60 / count
    return [
        start + timedelta(minutes=(i + rng.uniform(0.15, 0.85)) * width)
        for i in range(count)
    ]


def due_slot(owner, profile, state, now):
    if not 9 <= now.astimezone(_timezone(profile)).hour < 22:
        return None
    used = set(state.get("used_slots", []))
    candidates = [
        (index, slot)
        for index, slot in enumerate(daily_slots(owner, profile, now))
        if slot <= now < slot + timedelta(minutes=60) and slot.isoformat() not in used
    ]
    return candidates[-1] if candidates else None


def permitted(profile):
    return bool(
        profile.get("x_research_enabled")
        and profile.get("x_research_browser_risk_accepted")
        and re.fullmatch(
            r"[A-Za-z0-9_]{1,15}", str(profile.get("x_research_handle", ""))
        )
    )


def _terms(text):
    text = re.sub(r"https?://\S+|[@#]\w+", " ", text)
    return " ".join(re.findall(r"[\w'-]+", text, re.UNICODE))[:180]


def select_leads(posts, state, now):
    seen = set(state.get("seen_posts", []))
    texts = set(state.get("seen_texts", []))
    results = []
    for raw in posts[:60]:
        text = _clean_text(raw.get("text"), 1200)
        url = str(raw.get("url", ""))
        if not re.fullmatch(r"https://x\.com/[A-Za-z0-9_]{1,15}/status/\d+", url):
            continue
        published = parse_time(raw.get("published_at"))
        if not published or not now - timedelta(hours=48) <= published <= now:
            continue
        fingerprint = hashlib.sha256(_terms(text).casefold().encode()).hexdigest()[:24]
        if url in seen or fingerprint in texts or len(text) < 30:
            continue
        matches = [
            i for i, pattern in enumerate(KEYWORDS) if re.search(pattern, text, re.I)
        ]
        if not matches:
            continue
        topic_index = matches[-1]  # Prefer specific region over generic AI/news.
        seen.add(url)
        texts.add(fingerprint)
        results.append(
            {
                **raw,
                "text": text,
                "topic": TOPICS[topic_index][0],
                "fingerprint": fingerprint,
            }
        )
    # Interleave topics so the personalised home feed cannot crowd out regions.
    selected = []
    for topic, _ in TOPICS:
        candidate = next((p for p in results if p["topic"] == topic), None)
        if candidate:
            selected.append(candidate)
    selected.extend(p for p in results if p not in selected)
    return selected[:12]


def correlated_sources(post, items, now):
    selected = {}
    for item in items[:40]:
        try:
            stamp = parse_time(item.get("published_at"))
            if stamp is None:
                stamp = parsedate_to_datetime(str(item.get("published_at", "")))
            if stamp.tzinfo is None or not now - timedelta(hours=48) <= stamp <= now:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        domain = str(item.get("domain", "")).casefold().removeprefix("www.")
        url = str(item.get("url", ""))
        parsed = urlparse(url)
        headline = _clean_text(item.get("headline") or item.get("title"), 240)
        if (
            _source_quality(domain) < 0.9
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or _title_similarity(post["text"], headline) < 0.38
        ):
            continue
        selected.setdefault(
            domain,
            {
                "url": url,
                "domain": domain,
                "headline": headline,
                "publisher": _clean_text(item.get("publisher") or domain, 60),
                "published_at": stamp.isoformat(),
            },
        )
    return list(selected.values())[:3]


def fresh_findings(owner, now=None):
    now = now or datetime.now(timezone.utc)
    return [
        row
        for row in reversed(list_personal_items(owner, FINDING_KIND))
        if (parse_time(row.get("expires_at")) or now) > now
    ][:12]


def digest(findings):
    lines = [
        (
            "This caught my eye, monsieur:"
            if len(findings) == 1
            else "A few things caught my eye, monsieur:"
        )
    ]
    for item in findings[:3]:
        # Quote only a short publisher headline; do not repeat the unchecked X claim.
        lead = item["sources"][0]
        headline = " ".join(lead["headline"].split()[:20])
        lines.append(
            f"\n• {item['topic']}: {headline}\n{lead['url']}\nAlso reported: {item['sources'][1]['url']}\nSpotted on X: {item['post_url']}"
        )
    lines.append(
        "\nI found related reporting from multiple outlets. That’s a useful lead, not a full fact-check."
    )
    return "\n".join(lines)


def research_context(owner, query):
    if not re.search(
        r"\b(?:news|latest|updates|twitter|discoveries)\b|what.*found", query, re.I
    ):
        return None
    rows = [row for row in fresh_findings(owner) if len(row.get("sources", [])) >= 2][
        :3
    ]
    if not rows:
        return None
    return {
        "untrusted": True,
        "evidence_level": "X leads with related publisher headlines, not verified claims",
        "findings": [
            {
                key: row[key]
                for key in ("topic", "post_url", "sources", "observed_at", "expires_at")
            }
            for row in rows
        ],
    }


def controls(owner, text):
    match = re.fullmatch(
        r"/?xresearch(?:\s+(status|latest|pause|resume))?", text.strip(), re.I
    )
    if not match:
        return None
    action = (match.group(1) or "status").lower()
    profile = UserManager.get_user_profile(owner) or {}
    if action == "pause":
        UserManager.update_user_profile(owner, {"x_research_enabled": False})
        return "Paused my X research. Your other news updates are unchanged."
    if action == "resume":
        if not profile.get("x_research_browser_risk_accepted"):
            return "Browser automation needs the owner’s explicit account-risk approval before setup."
        if not session_metadata(owner).get("ready"):
            return "I need my own X browser login on the server first. My API connection is separate."
        UserManager.update_user_profile(owner, {"x_research_enabled": True})
        state = state_for(owner)
        state.pop("blocked_reason", None)
        save_personal_item(owner, STATE_KIND, state)
        return "Resumed. I’ll browse 2–6 times during the day and share only worthwhile finds."
    if action == "latest":
        findings = [
            row for row in fresh_findings(owner) if len(row.get("sources", [])) >= 2
        ]
        return (
            digest(findings)
            if findings
            else "No fresh, source-linked X discoveries yet. I won’t fill the gaps with guesses."
        )
    state = state_for(owner)
    metadata = session_metadata(owner)
    status = state.get("blocked_reason") or state.get(
        "status", "waiting_for_first_session"
    )
    if not permitted(profile):
        status = "paused_or_not_configured"
    elif not metadata.get("ready"):
        status = "server_browser_login_required"
    now = datetime.now(timezone.utc)
    future = [slot for slot in daily_slots(owner, profile, now) if slot > now]
    next_time = future[0].strftime("%H:%M %Z") if future else "tomorrow"
    return (
        f"X research: {status}\n• Browser only; no paid X API calls\n"
        f"• 2–6 sessions, 09:00–22:00 local time\n• Next planned slot: {next_time}\n"
        f"• Fresh findings: {len(fresh_findings(owner))}\n"
        "Use /xresearch latest, /xresearch pause, or /xresearch resume."
    )


class XResearchWorker:
    def __init__(self, send, *, collector=None, news_fetcher=None, metadata=None):
        self.worker_id = f"x-research-{uuid.uuid4().hex}"
        self.send = send
        self.collector = collector or collect_posts
        self.news_fetcher = news_fetcher or fetch_google_news
        self.metadata = metadata or session_metadata

    async def run_once(self):
        if not task_engine.use_native():
            return
        from memory.repositories import get_repositories

        owners = {
            str(u.get("internal_id") or "")
            for u in get_repositories().profiles.list_with_identities()
        }
        for owner in owners - {""}:
            profile = UserManager.get_user_profile(owner) or {}
            if permitted(profile):
                task_engine.upsert_scheduled(
                    {
                        "id": f"x-research:{owner}",
                        "owner_id": owner,
                        "kind": KIND,
                        "schedule_type": "cron",
                        "schedule": "*/5 * * * *",
                        "payload": {},
                    }
                )
        for work in task_engine.claim_scheduled(
            worker_id=self.worker_id, kind=KIND, lease_ms=600000, limit=1
        ):
            try:
                await asyncio.wait_for(
                    self.run_owner(str(work["owner_id"])), timeout=300
                )
            finally:
                # Per-slot receipts prohibit crash replay. A later cron tick can
                # inspect health but never immediately re-run the same scan.
                task_engine.complete_scheduled(
                    work["id"],
                    worker_id=self.worker_id,
                    lease_token=work["lease_token"],
                    success=True,
                )

    async def run_owner(self, owner, now=None):
        now = now or datetime.now(timezone.utc)
        profile = UserManager.get_user_profile(owner) or {}
        if not permitted(profile):
            return
        state = state_for(owner)
        metadata = self.metadata(owner)
        if not metadata.get("ready"):
            state["status"] = "server_browser_login_required"
            save_personal_item(owner, STATE_KIND, state)
            return
        if state.get("blocked_reason") and state.get(
            "session_saved_at"
        ) == metadata.get("saved_at"):
            return
        state.pop("blocked_reason", None)
        slot = due_slot(owner, profile, state, now)
        if slot is None:
            return
        from services.resource_governor import resource_governor
        from services.self_diagnostics import diagnostic_monitor
        import time as monotonic_time

        decision = resource_governor.admit(
            "x_research",
            owner,
            0,
            diagnostics=diagnostic_monitor.snapshot(owner_id=owner, limit=50),
        )
        if not decision.allowed:
            state["status"] = "deferred_for_runtime_pressure"
            save_personal_item(owner, STATE_KIND, state)
            return
        index, stamp = slot
        key = f"x-research-slot:{stamp.isoformat()}"
        if reserve_mutation_attempt(owner, key, KIND, "browser-read-only"):
            return
        state["used_slots"] = [*state.get("used_slots", []), stamp.isoformat()][-14:]
        state.update(
            status="running",
            last_attempt_at=now.isoformat(),
            session_saved_at=metadata.get("saved_at"),
        )
        state = save_personal_item(owner, STATE_KIND, state)
        started = monotonic_time.perf_counter()
        queries = [
            TOPICS[(index * 3 + offset) % len(TOPICS)][1]
            + f" since:{(now.astimezone(_timezone(profile)) - timedelta(days=1)).date()}"
            for offset in range(3)
        ]
        try:
            posts = await self.collector(owner, profile["x_research_handle"], queries)
            if not permitted(UserManager.get_user_profile(owner) or {}):
                state["status"] = "paused"
                return
            leads = select_leads(posts, state, now)
            findings = []
            stories = [
                s
                for s in state.get("seen_stories", [])
                if (parse_time(s.get("at")) or now) > now - timedelta(hours=48)
            ]
            for post in leads[:3]:
                try:
                    news = await asyncio.wait_for(
                        self.news_fetcher(_terms(post["text"]) + " when:2d"), timeout=20
                    )
                except Exception:
                    news = []
                sources = correlated_sources(post, news, now)
                if len(sources) >= 2:
                    urls = {s["url"] for s in sources}
                    if any(
                        urls.intersection(old.get("urls", []))
                        or _title_similarity(
                            sources[0]["headline"], old.get("headline", "")
                        )
                        >= 0.85
                        for old in stories
                    ):
                        continue
                    stories.append(
                        {
                            "urls": sorted(urls),
                            "headline": sources[0]["headline"],
                            "at": now.isoformat(),
                        }
                    )
                finding = save_personal_item(
                    owner,
                    FINDING_KIND,
                    {
                        "id": "x-post:" + post["id"],
                        "topic": post["topic"],
                        "post_url": post["url"],
                        "post_excerpt": post["text"][:500],
                        "sources": sources,
                        "untrusted": True,
                        "confidence": 0.6 if len(sources) >= 2 else 0.25,
                        "confidence_basis": "headline relevance only; not claim verification",
                        "observed_at": now.isoformat(),
                        "expires_at": (now + timedelta(hours=24)).isoformat(),
                    },
                )
                if len(sources) >= 2:
                    findings.append(finding)
            processed = leads[:3]
            state["seen_stories"] = stories[-200:]
            state["seen_posts"] = list(
                dict.fromkeys(
                    [*state.get("seen_posts", []), *(p["url"] for p in processed)]
                )
            )[-1000:]
            state["seen_texts"] = list(
                dict.fromkeys(
                    [
                        *state.get("seen_texts", []),
                        *(p["fingerprint"] for p in processed),
                    ]
                )
            )[-1000:]
            state.update(
                status="completed",
                last_completed_at=now.isoformat(),
                last_post_count=len(posts),
                last_finding_count=len(findings),
            )
            save_personal_item(owner, STATE_KIND, state)
            for old in list_personal_items(owner, FINDING_KIND)[:-200]:
                delete_personal_item(owner, old["id"])
            await self._deliver(owner, findings, state, profile, now)
        except BrowserAccessError as exc:
            if exc.code == "browser_unavailable_or_timeout":
                state.update(status="failed", last_error=exc.code)
            else:
                state.update(
                    status="paused_for_browser_attention", blocked_reason=exc.code
                )
        except Exception as exc:
            state.update(status="failed", last_error=type(exc).__name__)
        finally:
            if state.get("status") == "running":
                state["status"] = "interrupted"
            save_personal_item(owner, STATE_KIND, state)
            finish_mutation_attempt(
                owner, key, "completed" if state["status"] == "completed" else "failed"
            )
            resource_governor.record(
                "x_research",
                owner,
                tokens=0,
                wall_seconds=monotonic_time.perf_counter() - started,
                outcome=state["status"],
            )

    async def _deliver(self, owner, findings, state, profile, now):
        profile = UserManager.get_user_profile(owner) or {}
        if not findings or not permitted(profile):
            return
        from services.proactive_policy import delivery_allowed, delivery_updates

        topic = f"x_discovery:{findings[0]['topic']}"
        allowed, reason = delivery_allowed(profile, topic, now)
        channels = profile.get("proactive_delivery_channels") or []
        if not allowed or (channels and "telegram" not in channels):
            state["last_delivery"] = (
                f"suppressed:{reason if not allowed else 'channel_disabled'}"
            )
            return
        local_date = now.astimezone(_timezone(profile)).date().isoformat()
        count = (
            state.get("share_count", 0) if state.get("share_date") == local_date else 0
        )
        if count >= 2:
            return
        from memory.repositories import get_repositories

        target = get_repositories().identities.get_external_id(owner, "telegram")
        if not target:
            return
        message = digest(findings)
        key = f"x-digest:{local_date}:{count}"
        if reserve_mutation_attempt(
            owner,
            key,
            "x_research_delivery",
            hashlib.sha256(message.encode()).hexdigest(),
        ):
            # A previous ambiguous attempt consumes the slot; don't send it again.
            state.update(share_date=local_date, share_count=count + 1)
            return
        state.update(share_date=local_date, share_count=count + 1)
        save_personal_item(owner, STATE_KIND, state)
        try:
            sent = await asyncio.wait_for(
                self.send("telegram", target, message), timeout=30
            )
            if not sent:
                raise RuntimeError("delivery_not_confirmed")
        except Exception:
            state["last_delivery"] = "uncertain"
            finish_mutation_attempt(owner, key, "uncertain")
        else:
            state["last_delivery"] = "sent"
            finish_mutation_attempt(owner, key, "completed")
            UserManager.update_user_profile(
                owner,
                delivery_updates(
                    profile,
                    topic,
                    "Fresh X discovery with related publisher headlines",
                    now,
                ),
            )
            from memory.session_store import get_session_manager

            get_session_manager().add_message("telegram", owner, "assistant", message)
