"""Shared AI research packets for Curie and Letta; browser reads never use X APIs."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
import hashlib
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from defusedxml import ElementTree as ET

import httpx

from memory import UserManager
from memory.local_store import (
    delete_personal_item,
    finish_mutation_attempt,
    list_personal_items,
    reserve_mutation_attempt,
    save_personal_item,
)
from services.autonomous_learning import _clean_text, _timezone
from utils.temporal_context import parse_time

STATE = "ai_scout_state"
FINDING = "ai_scout_finding"
PREFERENCES = "ai_scout_preferences"
TOPICS = {
    "models": '("model release" OR "open weights" OR "new LLM")',
    "tools": '("AI tool" OR "AI coding" OR "AI IDE")',
    "agents": '("AI agent" OR "agent framework" OR "multi-agent")',
    "harnesses": '("agent harness" OR "context engineering" OR "MCP")',
    "local-ai": '("local LLM" OR "inference" OR "quantization")',
    "research": '("LLM evaluation" OR "AI benchmark" OR "reasoning model")',
}
# Explicit public project release feeds. New sources require an operator change;
# post text cannot redirect the reader to arbitrary URLs or install software.
FEEDS = (
    ("ollama/ollama", "local-ai"),
    ("ggml-org/llama.cpp", "local-ai"),
    ("vllm-project/vllm", "local-ai"),
    ("huggingface/transformers", "models"),
    ("huggingface/smolagents", "agents"),
    ("letta-ai/letta-code", "harnesses"),
    ("NousResearch/hermes-agent", "agents"),
    ("langchain-ai/langgraph", "agents"),
    ("openai/codex", "tools"),
    ("anthropics/claude-code", "tools"),
)
BLOGS = (
    ("OpenAI", "https://openai.com/news/rss.xml", "openai.com", "models"),
    (
        "Hugging Face",
        "https://huggingface.co/blog/feed.xml",
        "huggingface.co",
        "research",
    ),
)


def x_candidates(posts, now):
    patterns = {
        "harnesses": r"\bharness|context engineering|\bmcp\b|letta|hermes",
        "local-ai": r"local llm|quantiz|\bgguf\b|\bllama\.cpp\b|\bvllm\b|\bollama\b",
        "agents": r"\bai agents?\b|agent framework|multi.agent",
        "models": r"\bllms?\b|open.weights|\bqwen\b|\bglm\b|reasoning model",
        "tools": r"\bai tools?\b|\bai coding\b|\bcodex\b|claude code",
        "research": r"\bai benchmark|\bllm evaluat|\bai research",
    }
    results = []
    for post in posts[:60]:
        text = _clean_text(post.get("text"), 500)
        topic = next(
            (
                key
                for key, pattern in patterns.items()
                if re.search(pattern, text, re.I)
            ),
            None,
        )
        stamp = parse_time(post.get("published_at"))
        url = str(post.get("url", ""))
        if (
            not topic
            or not stamp
            or not now - timedelta(hours=48) <= stamp <= now
            or not re.fullmatch(r"https://x\.com/[A-Za-z0-9_]{1,15}/status/\d+", url)
        ):
            continue
        results.append(
            {
                "id": hashlib.sha256(url.encode()).hexdigest()[:20],
                "topic": topic,
                "title": text[:120],
                "url": url,
                "publisher": str(post.get("author", ""))[:20],
                "published_at": stamp.isoformat(),
                "excerpt": text,
                "evidence": "unverified_x_lead",
                "confidence": 0.25,
                "confidence_basis": "unverified social post; requires original-source verification",
                "untrusted": True,
            }
        )
    return results


def parse_blog_feed(body, publisher, domain, topic, now):
    if (
        len(body) > 2097152
        or b"<!DOCTYPE" in body.upper()
        or b"<!ENTITY" in body.upper()
    ):
        raise ValueError("feed_not_supported")
    root = ET.fromstring(body)
    result = []
    for entry in root.findall("./channel/item")[:25]:
        try:
            stamp = parsedate_to_datetime(entry.findtext("pubDate", ""))
        except (ValueError, TypeError):
            continue
        url = entry.findtext("link", "")
        parsed = urlparse(url)
        if (
            not stamp.tzinfo
            or not now - timedelta(hours=48) <= stamp <= now
            or parsed.scheme != "https"
            or parsed.netloc != domain
        ):
            continue
        title = _clean_text(entry.findtext("title", ""), 180)
        excerpt = _clean_text(entry.findtext("description", ""), 800)
        if not re.search(
            r"model|\bllm\b|agent|inferenc|benchmark|reasoning|codex|gpt[- .]\d|transformer|training|open.weights|context.window|\bapi\b|coding",
            title + " " + excerpt,
            re.I,
        ):
            continue
        result.append(
            {
                "id": hashlib.sha256(url.encode()).hexdigest()[:20],
                "topic": topic,
                "title": f"{publisher}: {title}",
                "url": url,
                "publisher": publisher,
                "published_at": stamp.isoformat(),
                "excerpt": excerpt,
                "evidence": "publication_feed",
                "confidence": 0.7,
                "confidence_basis": "publisher-hosted article; its claims are not independently verified",
                "untrusted": True,
            }
        )
    return result


def state_for(owner):
    rows = list_personal_items(owner, STATE)
    return dict(rows[-1]) if rows else {"id": "ai-scout-state-v1"}


def preferences(owner):
    rows = list_personal_items(owner, PREFERENCES)
    return dict(rows[-1]) if rows else {"id": "ai-scout-preferences-v1", "weights": {}}


def slots(profile, now):
    day = now.astimezone(_timezone(profile)).date()
    start = datetime.combine(
        day, time(11 if day.weekday() >= 5 else 9), tzinfo=_timezone(profile)
    )
    if profile.get("ai_scout_cadence") == "daily":
        return [datetime.combine(day, time(19, 30), tzinfo=_timezone(profile))]
    return [start + timedelta(minutes=210 * i) for i in range(4)]


def due_slot(profile, state, now):
    return next(
        (
            (i, slot)
            for i, slot in reversed(list(enumerate(slots(profile, now))))
            if slot <= now < slot + timedelta(hours=1)
            and slot.isoformat() not in state.get("used_slots", [])
        ),
        None,
    )


def feedback(owner, topic, direction):
    if topic not in TOPICS or direction not in {"more", "less", "reset"}:
        raise ValueError("Use an AI topic and more, less, or reset")
    item = preferences(owner)
    weights = item.setdefault("weights", {})
    weights[topic] = (
        0
        if direction == "reset"
        else max(
            -2, min(2, int(weights.get(topic, 0)) + (1 if direction == "more" else -1))
        )
    )
    item["updated_at"] = datetime.now(timezone.utc).isoformat()
    item["basis"] = "explicit_user_feedback"
    save_personal_item(owner, PREFERENCES, item)
    return weights


def parse_release_feed(body, repository, topic, now):
    if (
        len(body) > 2097152
        or b"<!DOCTYPE" in body.upper()
        or b"<!ENTITY" in body.upper()
    ):
        raise ValueError("feed_not_supported")
    root = ET.fromstring(body)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("a:entry", ns)[:12]:
        stamp = parse_time(
            entry.findtext("a:published", namespaces=ns)
            or entry.findtext("a:updated", namespaces=ns)
        )
        link = next(
            (
                node.get("href", "")
                for node in entry.findall("a:link", ns)
                if node.get("rel", "alternate") == "alternate"
            ),
            "",
        )
        parsed = urlparse(link)
        if not stamp or not now - timedelta(hours=48) <= stamp <= now:
            continue
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or not parsed.path.startswith("/" + repository + "/releases/tag/")
            or parsed.query
            or parsed.fragment
        ):
            continue
        headline = _clean_text(entry.findtext("a:title", namespaces=ns), 180)
        if not headline:
            continue
        items.append(
            {
                "id": hashlib.sha256(link.encode()).hexdigest()[:20],
                "topic": topic,
                "title": f"{repository}: {headline}",
                "url": link,
                "publisher": repository,
                "published_at": stamp.isoformat(),
                "excerpt": _clean_text(entry.findtext("a:content", namespaces=ns), 800),
                "evidence": "publisher_release",
                "confidence": 0.9,
                "confidence_basis": "release published by this project; performance claims are not independently verified",
                "untrusted": True,
            }
        )
    return items


async def fetch_releases(now, cache):
    gate = asyncio.Semaphore(3)
    items, errors = [], []
    async with httpx.AsyncClient(
        timeout=12, follow_redirects=False, headers={"User-Agent": "Curie-AI-Scout/1.0"}
    ) as client:

        async def fetch(repository, topic, feed_url=None, domain=None):
            async with gate:
                saved = cache.get(repository, {})
                headers = {"If-None-Match": saved["etag"]} if saved.get("etag") else {}
                try:
                    async with client.stream(
                        "GET",
                        feed_url or f"https://github.com/{repository}/releases.atom",
                        headers=headers,
                    ) as response:
                        if response.status_code == 304:
                            items.extend(saved.get("items", []))
                            return
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 2097152:
                                raise ValueError("feed_too_large")
                        parsed = (
                            parse_blog_feed(bytes(body), repository, domain, topic, now)
                            if domain
                            else parse_release_feed(bytes(body), repository, topic, now)
                        )
                        cache[repository] = {
                            "etag": response.headers.get("etag", "")[:256],
                            "items": parsed,
                        }
                        items.extend(parsed)
                except Exception as exc:
                    errors.append({"source": repository, "error": type(exc).__name__})

        await asyncio.gather(
            *(fetch(*feed) for feed in FEEDS),
            *(fetch(name, topic, url, domain) for name, url, domain, topic in BLOGS),
        )
    return items, errors


def rank_candidates(items, state, prefs, now):
    seen = set(state.get("seen_urls", []))
    stories = set(state.get("seen_stories", []))
    unique = {}
    for raw in items:
        stamp = parse_time(raw.get("published_at"))
        if (
            not stamp
            or not now - timedelta(hours=48) <= stamp <= now
            or raw.get("url") in seen
        ):
            continue
        if raw.get("topic") not in TOPICS or raw.get("evidence") not in {
            "publisher_release",
            "publication_feed",
            "unverified_x_lead",
        }:
            continue
        item = dict(raw)
        item["story_key"] = hashlib.sha256(
            " ".join(re.findall(r"[\w.-]+", item["title"].casefold())).encode()
        ).hexdigest()[:24]
        if item["story_key"] in stories:
            continue
        age = (now - stamp).total_seconds() / 3600
        strength = {
            "publisher_release": 10,
            "publication_feed": 8,
            "unverified_x_lead": 2,
        }[item["evidence"]]
        preview = bool(
            re.search(
                r"\b(alpha|beta|rc\d*|canary|nightly|preview)\b", item["title"], re.I
            )
        )
        item["score"] = round(
            strength
            - age / 12
            - (3 if preview else 0)
            - (2 if len(item.get("excerpt", "")) < 60 else 0)
            + max(-2, min(2, prefs.get("weights", {}).get(item["topic"], 0))),
            3,
        )
        unique.setdefault(item["url"], item)
    ranked = sorted(unique.values(), key=lambda item: (-item["score"], item["url"]))
    novel = []
    for item in ranked:
        if item["story_key"] not in stories:
            stories.add(item["story_key"])
            novel.append(item)
    ranked = novel
    selected, categories = [], set()
    for item in ranked:
        if item["topic"] not in categories and item["evidence"] != "unverified_x_lead":
            selected.append(item)
            categories.add(item["topic"])
    selected.extend(item for item in ranked if item not in selected)
    return selected[:8]


def latest(owner, now=None):
    now = now or datetime.now(timezone.utc)
    return [
        row
        for row in reversed(list_personal_items(owner, FINDING))
        if (parse_time(row.get("expires_at")) or now) > now
    ][:12]


def report(items, coverage):
    lines = ["A few AI updates worth a look, monsieur:"]
    for item in items[:3]:
        label = "Unverified X lead: " if item["evidence"] == "unverified_x_lead" else ""
        lines.append(f"\n• {label}{item['title']}")
        excerpt = _clean_text(item.get("excerpt", ""), 200)
        excerpt = re.sub(r"^What's Changed\s*", "", excerpt, flags=re.I)
        if len(excerpt) >= 60 and item["evidence"] != "unverified_x_lead":
            lines.append(
                "  Source notes: “"
                + excerpt.rstrip()
                + ("…" if len(excerpt) == 200 else "")
                + "”"
            )
        lines.append(item["url"])
    lines.append(
        "\nThese are source announcements and leads. I haven’t independently tested their claims."
    )
    if coverage.get("x") != "read":
        lines.append("X browsing is unavailable; these came from public release feeds.")
    lines.append(
        "\nTell me /aiscout more agents or /aiscout less local-ai to tune the mix."
    )
    return "\n".join(lines)


def controls(owner, text):
    match = re.fullmatch(
        r"/?aiscout(?:\s+(status|latest|pause|resume|daily|frequent|more|less|reset)(?:\s+([a-z-]+))?)?",
        text.strip(),
        re.I,
    )
    if not match:
        return None
    action, topic = (match.group(1) or "status").lower(), (match.group(2) or "").lower()
    if action in {"more", "less", "reset"}:
        if topic not in TOPICS:
            return "Choose models, tools, agents, harnesses, local-ai, or research."
        feedback(owner, topic, action)
        return f"Noted — I’ll adjust {topic} in your AI updates."
    if action in {"pause", "resume", "daily", "frequent"}:
        fields = {"ai_scout_enabled": action != "pause"}
        if action in {"daily", "frequent"}:
            fields["ai_scout_cadence"] = action
        UserManager.update_user_profile(owner, fields)
        return (
            "AI updates paused."
            if action == "pause"
            else "AI updates are on. I’ll keep them focused and avoid repeats."
        )
    state = state_for(owner)
    if action == "latest":
        findings = latest(owner)
        return (
            report(findings, state.get("coverage", {}))
            if findings
            else "No fresh AI findings saved yet."
        )
    profile = UserManager.get_user_profile(owner) or {}
    cadence = (
        "daily at 19:30"
        if profile.get("ai_scout_cadence") == "daily"
        else "weekdays 09:00, 12:30, 16:00, 19:30; weekends 11:00, 14:30, 18:00, 21:30"
    )
    return f"AI scout: {'enabled' if profile.get('ai_scout_enabled') else 'paused'}\n• Checks: {cadence} ({profile.get('timezone', 'UTC')})\n• Last run: {state.get('status', 'not_run')}\n• X: {state.get('coverage', {}).get('x', 'not_verified')}\n• Letta: {state.get('reviewer', 'not_run')}\n• No paid X API usage"


class AIScoutWorker:
    def __init__(
        self, send, *, fetcher=None, collector=None, metadata=None, reviewer=None
    ):
        from connectors.x_browser import collect_posts, session_metadata
        from services.ai_scout_reviewer import review

        self.send, self.fetcher = send, fetcher or fetch_releases
        self.collector, self.metadata = (
            collector or collect_posts,
            metadata or session_metadata,
        )
        self.reviewer = reviewer or review

    async def run_owner(self, owner, now=None):
        from services.resource_governor import resource_governor
        from services.self_diagnostics import diagnostic_monitor
        from connectors.x_browser import BrowserAccessError
        import time as clock

        now = now or datetime.now(timezone.utc)
        profile = UserManager.get_user_profile(owner) or {}
        if not profile.get("ai_scout_enabled"):
            return
        state = state_for(owner)
        due = due_slot(profile, state, now)
        if due is None:
            return
        decision = resource_governor.admit(
            "x_research",
            owner,
            128,
            diagnostics=diagnostic_monitor.snapshot(owner_id=owner, limit=50),
        )
        if not decision.allowed:
            return
        index, slot = due
        key = "ai-scout:" + slot.isoformat()
        if reserve_mutation_attempt(owner, key, STATE, "read-only"):
            return
        started = clock.perf_counter()
        reserved_output_tokens = 0
        state.update(
            status="running",
            used_slots=[*state.get("used_slots", []), slot.isoformat()][-12:],
        )
        save_personal_item(owner, STATE, state)
        try:
            coverage, posts = {"x": "not_authenticated"}, []
            metadata = self.metadata(owner)
            allowed = profile.get("x_research_browser_risk_accepted") and profile.get(
                "x_research_enabled"
            )
            blocked = state.get("x_blocked_session") == metadata.get(
                "saved_at"
            ) and bool(state.get("x_blocked_session"))
            if metadata.get("ready") and allowed and not blocked:
                queries = [
                    list(TOPICS.values())[(index * 3 + n) % len(TOPICS)]
                    + f" since:{(now - timedelta(days=1)).date()}"
                    for n in range(3)
                ]
                try:
                    posts = await asyncio.wait_for(
                        self.collector(owner, profile["x_research_handle"], queries),
                        timeout=90,
                    )
                    coverage["x"] = "read"
                except BrowserAccessError as exc:
                    coverage["x"] = exc.code
                    if exc.code != "browser_unavailable_or_timeout":
                        state["x_blocked_session"] = metadata.get("saved_at")
                except Exception:
                    coverage["x"] = "unavailable"
            elif blocked:
                coverage["x"] = "paused_after_restriction"
            items, errors = await asyncio.wait_for(
                self.fetcher(now, state.setdefault("feed_cache", {})), timeout=45
            )
            coverage.update(
                x_posts=len(posts), release_items=len(items), feed_errors=errors
            )
            candidates = rank_candidates(
                items + x_candidates(posts, now), state, preferences(owner), now
            )
            # X leads remain explicitly unverified in all storage and output.
            # Agent suggestions cannot invent URLs, facts, sources, or access claims.
            selected, reviewer = (
                await self.reviewer(candidates, preferences(owner))
                if candidates
                else ([], "not_needed")
            )
            if reviewer.startswith("letta_") and candidates:
                reserved_output_tokens = 128
            state["model_output_tokens_reserved"] = reserved_output_tokens
            state["model_token_usage_basis"] = (
                "output ceiling; input tokens not measured"
            )
            allowed_ids = {item["id"]: item for item in candidates}
            approved = []
            for item_id in selected[:3]:
                if item_id in allowed_ids and allowed_ids[item_id] not in approved:
                    item = allowed_ids[item_id]
                    if item["evidence"] != "unverified_x_lead" or not any(
                        p["evidence"] == "unverified_x_lead" for p in approved
                    ):
                        approved.append(item)
            if not approved:
                approved = candidates[:1]
            for item in approved:
                save_personal_item(
                    owner,
                    FINDING,
                    {
                        **item,
                        "observed_at": now.isoformat(),
                        "expires_at": (
                            parse_time(item["published_at"]) + timedelta(hours=48)
                        ).isoformat(),
                    },
                )
            state.update(
                status="completed" if not errors or items else "sources_unavailable",
                coverage=coverage,
                reviewer=reviewer,
                last_completed_at=now.isoformat(),
            )
            state["seen_urls"] = list(
                dict.fromkeys(
                    [*state.get("seen_urls", []), *(item["url"] for item in approved)]
                )
            )[-1000:]
            state["seen_stories"] = list(
                dict.fromkeys(
                    [
                        *state.get("seen_stories", []),
                        *(item["story_key"] for item in approved),
                    ]
                )
            )[-1000:]
            for old in list_personal_items(owner, FINDING)[:-200]:
                delete_personal_item(owner, old["id"])
            to_deliver = approved
            if index == len(slots(profile, now)) - 1:
                attempted = set(state.get("reported_urls", []))
                to_deliver = [
                    item for item in latest(owner, now) if item["url"] not in attempted
                ][:3]
            await self.deliver(owner, to_deliver, state, key, now)
        except Exception as exc:
            state.update(status="failed", last_error=type(exc).__name__)
        finally:
            if state.get("status") == "running":
                state["status"] = "interrupted"
            save_personal_item(owner, STATE, state)
            finish_mutation_attempt(
                owner, key, "completed" if state["status"] == "completed" else "failed"
            )
            resource_governor.record(
                "x_research",
                owner,
                tokens=reserved_output_tokens,
                wall_seconds=clock.perf_counter() - started,
                outcome=state["status"],
            )

    async def deliver(self, owner, findings, state, slot_key, now):
        from services.proactive_policy import delivery_allowed, delivery_updates
        from memory.repositories import get_repositories

        profile = UserManager.get_user_profile(owner) or {}
        if not findings or not profile.get("ai_scout_enabled"):
            return
        # The explicit scout schedule has its own one-hour topic guard. Preserve
        # global caps, quiet hours, snooze and explicit negative-feedback backoff.
        weekend = now.astimezone(_timezone(profile)).weekday() >= 5
        policy = {
            **profile,
            "proactive_topic_cooldown_hours": 1,
            "proactive_quiet_hours": {"start": 0, "end": 11 if weekend else 8},
        }
        allowed, reason = delivery_allowed(policy, "ai_updates", now)
        channels = profile.get("proactive_delivery_channels") or []
        if not allowed or (channels and "telegram" not in channels):
            state["delivery"] = "suppressed:" + (
                reason if not allowed else "channel_disabled"
            )
            return
        target = get_repositories().identities.get_external_id(owner, "telegram")
        if not target:
            state["delivery"] = "no_telegram_identity"
            return
        message = report(findings, state["coverage"])
        key = slot_key + ":delivery"
        if reserve_mutation_attempt(
            owner,
            key,
            "ai_scout_delivery",
            hashlib.sha256(message.encode()).hexdigest(),
        ):
            return
        # An ambiguous send must not reappear in the evening recap either.
        state["reported_urls"] = list(
            dict.fromkeys(
                [*state.get("reported_urls", []), *(item["url"] for item in findings)]
            )
        )[-1000:]
        save_personal_item(owner, STATE, state)
        try:
            if not await asyncio.wait_for(
                self.send("telegram", target, message), timeout=30
            ):
                raise RuntimeError("delivery_not_confirmed")
        except Exception:
            state["delivery"] = "uncertain"
            finish_mutation_attempt(owner, key, "uncertain")
        else:
            state["delivery"] = "sent"
            finish_mutation_attempt(owner, key, "completed")
            UserManager.update_user_profile(
                owner,
                delivery_updates(
                    profile, "ai_updates", "Source-linked AI releases", now
                ),
            )
            from memory.session_store import get_session_manager

            get_session_manager().add_message("telegram", owner, "assistant", message)
