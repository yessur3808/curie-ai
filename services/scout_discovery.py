"""Editorial eligibility for capability discoveries, never product endorsements."""

import re
from datetime import datetime, timedelta, timezone
import hashlib

WATCHED_TOOLS = r"\b(?:t3\s*code|zeron|paseo|devin|hermes|(?:open)?mausbot|openbot|ADE(?:Mark)?|letta|codex|claude code)\b"
CAPABILITY = r"\b(?:adds?|added|introduc\w*|launch\w*|enables?|supports?|new|now|can|allows?|build|built|setup|set up|walkthrough|tutorial|compar\w*|workflow|feature|plugin|integration|memory|orchestrat\w*|remote|vision|multimodal|reasoning|context|coding|tool.call\w*|faster|outperform\w*|better|improv\w*)\b"


def capability_discovery(item):
    title = str(item.get("title", ""))
    excerpt = str(item.get("excerpt", ""))
    text = title + " " + excerpt
    subject = bool(re.search(WATCHED_TOOLS, text, re.I)) or bool(
        re.search(
            r"agent (?:harness|workspace|framework|setup)|coding (?:agent|tool|assistant)|persistent memory|remote coding|agentic",
            text,
            re.I,
        )
    )
    model = bool(
        re.search(
            r"open[- ](?:source|weights?)|\b(?:Qwen|DeepSeek|Mistral|Gemma|GLM|Kimi|LLM)\b",
            text,
            re.I,
        )
    )
    if model and not subject:
        model = bool(
            re.search(
                r"coding|reasoning|tool.call|multimodal|vision|context|memory|hardware|faster|better|outperform|improv",
                excerpt,
                re.I,
            )
        )
    # A name/version alone cannot qualify. Require a source-described capability.
    return (
        (subject or model)
        and len(excerpt.strip()) >= 60
        and bool(re.search(CAPABILITY, excerpt, re.I))
    )


def devin_updates(body, now):
    """Parse dated official markdown; no arbitrary links or fetched instructions."""
    if len(body) > 2097152:
        raise ValueError("feed_too_large")
    text = body.decode("utf-8")
    sections = re.split(r"(?m)^\*\*([A-Z][a-z]+ \d{1,2}, \d{4})\*\*\s*$", text)
    items = []
    for i in range(1, len(sections) - 1, 2):
        try:
            stamp = datetime.strptime(sections[i], "%B %d, %Y").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if not now - timedelta(days=7) <= stamp <= now:
            continue
        # Keep individual features rather than swallowing a whole daily changelog.
        features = re.split(r"(?m)^\*\*([^*\n]+)\*\*\s*$", sections[i + 1])
        for j in range(1, len(features) - 1, 2):
            title = features[j].strip()
            excerpt = re.split(r"(?m)^### ", features[j + 1])[0].strip()
            excerpt = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", excerpt)
            if len(excerpt) < 60:
                continue
            # Date-anchor format verified against the official HTML page.
            url = "https://docs.devin.ai/release-notes/overview#" + stamp.strftime(
                "%B-%d-%Y"
            ).lower().replace("-0", "-")
            items.append(
                {
                    "id": hashlib.sha256((url + title).encode()).hexdigest()[:20],
                    "topic": "agents",
                    "title": "Devin: " + title,
                    "url": url,
                    "publisher": "Devin",
                    "published_at": stamp.isoformat(),
                    "excerpt": excerpt[:800],
                    "evidence": "publication_feed",
                    "confidence": 0.7,
                    "confidence_basis": "Official feature announcement; not independently tested",
                    "untrusted": True,
                }
            )
    return items[:24]
