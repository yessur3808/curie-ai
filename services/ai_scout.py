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
        "tools": r"\bai tools?\b|\bai coding\b|\bcodex\b|claude code|typescript|developer tool|code review|software architecture|web performance",
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


def set_focus(owner, focus):
    """Persist an explicitly requested reading focus without resetting topic feedback."""
    if focus not in {"engineering_lead", "general"}:
        raise ValueError("Unknown AI scout focus")
    item = preferences(owner)
    item.update(
        focus=focus,
        updated_at=datetime.now(timezone.utc).isoformat(),
        basis="explicit_user_feedback",
    )
    save_personal_item(owner, PREFERENCES, item)
    return item


# Operator-curated discovery sources; familiarity never increases evidence confidence.
ENGINEERING_ACCOUNTS = (
    "theo",
    "ThePrimeagen",
    "simonw",
    "addyosmani",
    "swyx",
    "steipete",
    "Teknium",
    "charlespacker",
)


def set_source_preferences(owner):
    item = preferences(owner)
    item.update(
        preferred_accounts=list(ENGINEERING_ACCOUNTS),
        discovery_focus="agent_workflows",
        source_basis="user_requested_theo_primeagen_and_similar_engineering_accounts",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    save_personal_item(owner, PREFERENCES, item)
    return item


def discovery_queries(prefs, index, now):
    since = f" since:{(now - timedelta(days=1)).date()}"
    accounts = [
        handle
        for handle in prefs.get("preferred_accounts", [])[:8]
        if isinstance(handle, str) and re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle)
    ]
    if accounts:
        # Named anchors always get coverage; peers share a second bounded search.
        groups = (accounts[:2], accounts[2:])
        queries = [
            "(" + " OR ".join("from:" + h for h in group) + ")" + since
            for group in groups
            if group
        ]
        queries.append(list(TOPICS.values())[index % len(TOPICS)] + since)
        return queries[:3]
    return [
        list(TOPICS.values())[(index * 3 + n) % len(TOPICS)] + since for n in range(3)
    ]


def useful_summary(item):
    """Keep a concrete source claim, dropping release plumbing and attribution noise."""
    text = _clean_text(item.get("excerpt", ""), 1500)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+by @?[\w-]+(?:\s+in)?", "", text)
    text = re.sub(
        r"What's Changed|Full Changelog[^.!?]*|New Contributors[^.!?]*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"[#*`<>\[\]]", "", text)
    text = " ".join(text.split()).strip(" :-")
    return text[:240].rsplit(" ", 1)[0] + "…" if len(text) > 240 else text


def workflow_discovery(item):
    text = item.get("title", "") + " " + item.get("excerpt", "")
    return bool(
        re.search(
            r"agent|harness|orchestrat|workflow|developer tool|coding tool|"
            r"t3 code|paseo|devin|hermes|letta|remote coding|persistent memory",
            text,
            re.I,
        )
    )


def engineering_angle(item):
    """Suggested experiments, not claims about untested product capabilities."""
    text = (item.get("title", "") + " " + item.get("excerpt", "")).casefold()
    angles = (
        (
            r"\b(security|sandbox|permissions?|vulnerabilit\w*|credential\w*)\b",
            "This catches my eye for safer team adoption.",
            "Review permissions and data access in a sandbox before connecting a real repository.",
        ),
        (
            r"\b(eval\w*|test\w*|regression\w*|benchmark\w*)\b",
            "I’d put this on our evaluation shortlist.",
            "Use a small set of your own engineering tasks; compare failure cases, not just the headline score.",
        ),
        (
            r"\b(debug\w*|tracing|observability|profiling)\b",
            "The debugging angle looks worth exploring.",
            "Try one reproducible failure and see whether it becomes easier to diagnose.",
        ),
        (
            r"\b(inference|latency|memory|quantiz\w*|deploy\w*|throughput)\b",
            "I’m curious about the operational trade-offs here.",
            "Compare latency, memory and cost on the same small workload before changing your stack.",
        ),
        (
            r"\b(api|sdk|mcp|integration\w*|workflow\w*|coding|code review)\b",
            "This is a candidate for improving the development workflow.",
            "Try it on one disposable task; compare time saved, correctness and integration effort.",
        ),
    )
    for pattern, why, experiment in angles:
        if re.search(pattern, text):
            return why, experiment
    return (
        "Let’s see whether this earns a place in your toolkit.",
        "Read the linked example and test one relevant use case; check license, pricing and data handling before team adoption.",
    )


def engineering_score(item):
    text = (item.get("title", "") + " " + item.get("excerpt", "")).casefold()
    useful = re.search(
        r"\b(security|sandbox|permissions?|eval\w*|test\w*|regression\w*|"
        r"debug\w*|tracing|observability|inference|latency|memory|quantiz\w*|"
        r"deploy\w*|api|sdk|mcp|integration\w*|workflow\w*|coding|typescript|architecture|performance|developer tools?)\b",
        text,
    )
    housekeeping = re.search(
        r"\b(bump version|version bump|typos?|chore|formatting)\b", text
    )
    return 3 if useful else (-3 if housekeeping else 0)


def slots(profile, now):
    zone = _timezone(profile)
    day = now.astimezone(zone).date()
    if profile.get("ai_scout_cadence") == "daily":
        return [datetime.combine(day, time(19, 30), tzinfo=zone)]
    hours = (11, 15, 19, 23) if day.weekday() >= 5 else (8, 13, 16, 22)
    return [datetime.combine(day, time(hour), tzinfo=zone) for hour in hours]


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
        if prefs.get("focus") == "engineering_lead":
            # A release existing is not enough to interrupt the reader.
            if engineering_score(raw) < 3 or len(useful_summary(raw)) < 60:
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
            + (
                engineering_score(item)
                if prefs.get("focus") == "engineering_lead"
                else 0
            )
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
    if prefs.get("discovery_focus") == "agent_workflows":
        selected.sort(key=lambda item: not workflow_discovery(item))
    preferred = {
        h.casefold() for h in prefs.get("preferred_accounts", []) if isinstance(h, str)
    }
    leads = [
        item
        for item in ranked
        if item["evidence"] == "unverified_x_lead"
        and urlparse(item["url"]).path.split("/")[1].casefold() in preferred
    ][:2]
    # Keep requested voices visible to the reviewer even during release-heavy days.
    # They remain unverified and the final digest still caps X leads at one.
    return ([item for item in selected if item not in leads][: 8 - len(leads)] + leads)[
        :8
    ]


def latest(owner, now=None):
    now = now or datetime.now(timezone.utc)
    return [
        row
        for row in reversed(list_personal_items(owner, FINDING))
        if (parse_time(row.get("expires_at")) or now) > now
    ][:12]


def report_parts(items, coverage, prefs=None):
    """One self-contained, bounded card per finding; no raw changelog dump."""
    parts = []
    for index, item in enumerate(items[:3], 1):
        title = re.sub(r"[\n*`<>\[\]]", "", item["title"])[:110]
        why, experiment = engineering_angle(item)
        summary = useful_summary(item)
        label = (
            "Unverified X lead"
            if item["evidence"] == "unverified_x_lead"
            else "Source announcement"
        )
        lines = [f"**Curie’s engineering brief · {index}/{min(3, len(items))}**"]
        if index == 1:
            lines.append("A little inspiration for your next build, monsieur.")
        lines.extend(
            [
                f"\n**{title}**",
                f"\n**What changed:** {summary or 'See the source for details.'}",
                f"\n**Why it matters:** {why}",
                f"\n**Try this:** {experiment}",
                f"\n[{label}]({item['url']}) · Not independently tested.",
            ]
        )
        if coverage.get("x") != "read" and index == min(3, len(items)):
            lines.append("\nX browsing is unavailable; this edition uses public feeds.")
        parts.append("\n".join(lines))
    return parts


def report(items, coverage, prefs=None):
    return "\n\n".join(report_parts(items, coverage, prefs))


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
            report(findings, state.get("coverage", {}), preferences(owner))
            if findings
            else "No fresh AI findings saved yet."
        )
    profile = UserManager.get_user_profile(owner) or {}
    cadence = (
        "daily at 19:30"
        if profile.get("ai_scout_cadence") == "daily"
        else "weekdays 08:00, 13:00, 16:00, 22:00; weekends 11:00, 15:00, 19:00, 23:00"
    )
    return f"AI scout: {'enabled' if profile.get('ai_scout_enabled') else 'paused'}\n• Checks: {cadence} ({profile.get('timezone', 'UTC')})\n• Last run: {state.get('status', 'not_run')}\n• X: {state.get('coverage', {}).get('x', 'not_verified')}\n• Letta: {state.get('reviewer', 'not_run')}\n• No paid X API usage"


def recent_diagnostics(snapshot, now):
    """Historical slow turns must not keep background work paused forever."""
    records = [
        item
        for item in snapshot.get("records", [])
        if (stamp := parse_time(item.get("recorded_at")))
        and now - timedelta(minutes=15) <= stamp <= now
    ]
    latencies = sorted(float(item.get("latency_ms") or 0) for item in records)
    return {
        "last": records[-1] if records else {},
        "p95_latency_ms": (
            latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
            if latencies
            else 0
        ),
    }


class AIScoutWorker:
    def __init__(
        self,
        send,
        *,
        fetcher=None,
        collector=None,
        metadata=None,
        reviewer=None,
        clock=None,
    ):
        from connectors.x_browser import collect_posts, session_metadata
        from services.ai_scout_reviewer import review

        self.clock = clock or (lambda: datetime.now(timezone.utc))
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

        use_live_clock = now is None
        now = now or self.clock()
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
            diagnostics=recent_diagnostics(
                diagnostic_monitor.snapshot(owner_id=owner, limit=50), now
            ),
        )
        if not decision.allowed and decision.reason not in {
            "runtime pressure is severe",
            "daily token budget reached",
        }:
            return
        heavy_allowed = decision.allowed and decision.max_tokens >= 128
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
            if metadata.get("ready") and allowed and not blocked and heavy_allowed:
                queries = discovery_queries(preferences(owner), index, now)
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
            elif metadata.get("ready") and allowed and not heavy_allowed:
                coverage["x"] = "deferred_resource_pressure"
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
            if candidates and heavy_allowed:
                selected, reviewer = await self.reviewer(candidates, preferences(owner))
            elif candidates:
                selected = [item["id"] for item in candidates[:3]]
                reviewer = "deferred_resource_budget_deterministic_ranking"
            else:
                selected, reviewer = [], "not_needed"
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
            if selected and index == len(slots(profile, now)) - 1:
                attempted = set(state.get("reported_urls", []))
                to_deliver = [
                    item for item in latest(owner, now) if item["url"] not in attempted
                ][:3]
            if not to_deliver and state.get("delivery") != "uncertain":
                state["delivery"] = "no_new_items"
            # Research may cross midnight; enforce quiet hours at delivery time.
            delivery_now = self.clock() if use_live_clock else now
            await self.deliver(
                owner, to_deliver, state, key, delivery_now, live_clock=use_live_clock
            )
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

    async def deliver(self, owner, findings, state, slot_key, now, live_clock=False):
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
        prefs = preferences(owner)
        if prefs.get("focus") == "engineering_lead":
            findings = [
                item
                for item in findings
                if engineering_score(item) >= 3 and len(useful_summary(item)) >= 60
            ]
        if not findings:
            state["delivery"] = "no_relevant_items"
            return
        parts = report_parts(findings, state["coverage"], prefs)
        message = "\n\n".join(parts)
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
            state["delivery_parts_confirmed"] = 0
            for part in parts:
                # Recheck actual wall time before EVERY bubble, including midnight crossings.
                part_now = self.clock() if live_clock else now
                weekend = part_now.astimezone(_timezone(profile)).weekday() >= 5
                policy["proactive_quiet_hours"] = {
                    "start": 0,
                    "end": 11 if weekend else 8,
                }
                allowed, reason = delivery_allowed(policy, "ai_updates", part_now)
                if not allowed:
                    raise RuntimeError("delivery_suppressed:" + reason)
                if not await asyncio.wait_for(
                    self.send("telegram", target, part), timeout=30
                ):
                    raise RuntimeError("delivery_not_confirmed")
                state["delivery_parts_confirmed"] += 1
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
