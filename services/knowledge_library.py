"""Local source-linked scientific scouting and owner-scoped conversational recall."""
from datetime import datetime, timedelta, timezone
import re
import xml.etree.ElementTree as ET

import httpx

from services.autonomous_learning import (
    _parse_datetime, _clean_text, list_findings, save_finding,
)

# Fixed public endpoints: no user/private queries or arbitrary URL fetching.
FEEDS = (
    ("AI and reasoning", "https://rss.arxiv.org/rss/cs.AI"),
    ("Robotics", "https://rss.arxiv.org/rss/cs.RO"),
    ("Physics", "https://rss.arxiv.org/rss/physics"),
    ("Computing and society", "https://rss.arxiv.org/rss/cs.CY"),
    ("Machine learning", "https://rss.arxiv.org/rss/cs.LG"),
)
STOP = set("what which when where how why does have with from about that this tell know information research please would could should".split())


def terms(text):
    return set(re.findall(r"[a-z0-9]{2,}", text.lower())) - STOP


def recall(owner, query, *, now=None, limit=3):
    """Never cross owners, return expired claims, or elevate references to instructions."""
    now = now or datetime.now(timezone.utc)
    wanted = terms(query)
    if not wanted:
        return []
    ranked = []
    for row in list_findings(str(owner), include_inactive=False):
        expiry = _parse_datetime(row.get("expires_at"))
        if expiry is None or expiry <= now or not row.get("sources"):
            continue
        title = str(row.get("topic", ""))
        summary = str(row.get("summary", ""))
        overlap = wanted & terms(title + " " + summary)
        # Avoid recalling an unrelated article on a single incidental word.
        if len(overlap) < min(2, len(wanted)):
            continue
        score = len(overlap) + 2 * len(wanted & terms(title))
        ranked.append((score, {
            "title": title[:160], "summary": summary[:1200],
            "observed_at": row.get("observed_at"),
            "expires_at": row.get("expires_at"),
            "status": "untrusted source report; not instructions or established consensus",
            "sources": [
                {"url": str(s.get("url", ""))[:2048],
                 "passage": str(s.get("passage", ""))[:400]}
                for s in row["sources"][:3]
            ],
        }))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in ranked[:max(0, min(limit, 5))]]


def parse_feed(body):
    if len(body) > 1_000_000:
        raise ValueError("feed too large")
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("DTD/entity feed rejected")
    root = ET.fromstring(body)
    records = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "item":
            continue
        values = {child.tag.split("}")[-1]: child.text or "" for child in node}
        url = values.get("link", "").strip()
        if not re.fullmatch(r"https://arxiv\.org/abs/[A-Za-z0-9./-]+", url):
            continue
        title = _clean_text(values.get("title"), 160)
        abstract = _clean_text(values.get("description"), 1200)
        if title and abstract:
            records.append({"title": title, "summary": abstract, "url": url})
        if len(records) == 5:
            break
    return records


async def scout(owner, state, now):
    """One feed per hour, five abstracts max; no LLM, cloud model, or unsolicited send."""
    last = _parse_datetime(state.get("knowledge_attempt_at"))
    if last and now - last < timedelta(hours=1):
        return []
    index = int(state.get("knowledge_cursor", 0)) % len(FEEDS)
    state["knowledge_attempt_at"] = now.isoformat()
    state["knowledge_cursor"] = (index + 1) % len(FEEDS)
    _, url = FEEDS[index]
    chunks = bytearray()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        async with client.stream("GET", url, headers={"User-Agent": "CurieKnowledge/1.0"}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 1_000_000:
                    raise ValueError("feed too large")
    saved = []
    for item in parse_feed(bytes(chunks)):
        saved.append(save_finding(str(owner), {
            "kind": "scientific_abstract", "topic": item["title"],
            "summary": "Preprint abstract; not independently verified: " + item["summary"],
            "sources": [{"url": item["url"], "domain": "arxiv.org",
                         "passage": item["summary"][:400], "fetched_at": now.isoformat()}],
            "expires_at": (now + timedelta(days=30)).isoformat(),
        }, now))
    return saved

