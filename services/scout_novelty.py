"""Conservative, local novelty checks for engineering announcements."""

import re


def substantive_text(item):
    text = str(item.get("excerpt", ""))
    # Feed excerpts often flatten an entire changelog and its download table.
    text = re.split(
        r"\b(?:Full Changelog|New Contributors|Downloads|Attestations|Website:)",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+by\s+@[\w-]+(?:\[bot\])?(?:\s+in)?\s*", " ", text)
    text = re.sub(r"\(?\s*#\d+\s*\)?", " ", text)
    text = re.sub(
        r"What['’]s Changed|New Features|Changes since [\w.=+-]+", "", text, flags=re.I
    )
    # Keep user-facing fixes/features, not tests/docs/build bookkeeping. Conventional
    # commit markers remain useful boundaries even after the feed loses newlines.
    parts = re.split(
        r"(?=\b(?:feat|fix|perf|test|tests|docs|chore|ci|build)(?:\([^)]*\))?:)", text
    )
    parts = [
        p
        for p in parts
        if not re.match(r"(?:test|tests|docs|chore|ci|build)(?:\([^)]*\))?:", p, re.I)
    ]
    return " ".join(" ".join(parts).split()).strip(" :-")


def release_noise(item):
    if item.get("evidence") != "publisher_release":
        return False
    title = str(item.get("title", ""))
    excerpt = str(item.get("excerpt", "")).strip()
    substantive = substantive_text(item)
    packaging_only = bool(
        re.search(r"\bDownloads|Smoke status|OS Architecture\b", excerpt, re.I)
        and not re.search(
            r"\b(?:feat|fix|perf|added|adds|fixed|fixes|improved|improves|introduces|supports|enables)\b",
            substantive,
            re.I,
        )
    )
    return bool(
        (
            re.search(r"\b(?:canary|nightly)\b", title, re.I)
            and not (
                item.get("capability_preview")
                and re.search(
                    r"\b(?:feat|adds?|added|introduc\w*|enables?|new feature)\b",
                    substantive,
                    re.I,
                )
            )
        )
        or re.search(r"\babandoned\b|\brc[.\d-]", title, re.I)
        or re.match(r'\{\s*"(?:attempt|schema|version|autopublish)"\s*:', excerpt)
        or packaging_only
        or len(substantive) < 60
    )


def content_terms(item):
    text = substantive_text(item).casefold()
    # Version/commit churn is not a changed finding; preserve quantities and
    # negations so speed, risk, and factual changes are not silently discarded.
    text = re.sub(r"\bv?\d+\.\d+\.\d+(?:[+.-][\w.-]+)?\b", "", text)
    text = re.sub(r"\b[0-9a-f]{12,40}\b", "", text)
    return tuple(re.findall(r"\b[\w-]+\b", text))


def same_content(left, right):
    left_words, right_words = content_terms(left), content_terms(right)
    a, b = set(left_words), set(right_words)
    if min(len(a), len(b)) < 8:
        return False
    if a == b:
        return left_words == right_words
    # Only near-identical wording from the same publisher; not a claim of full
    # semantic similarity. Any changed numeric claim or negation is meaningful.
    if not left.get("publisher") or left.get("publisher") != right.get("publisher"):
        return False
    changed = a ^ b
    if any(
        re.search(r"\d", word)
        or word in {"no", "not", "without", "disabled", "enabled"}
        for word in changed
    ):
        return False
    return len(a & b) / len(a | b) >= 0.92


def novel_engineering(items, history=(), *, one_per_publisher=False):
    selected = []
    publishers = set()
    for item in items:
        publisher = item.get("publisher") or item.get("url")
        if release_noise(item) or (one_per_publisher and publisher in publishers):
            continue
        if any(same_content(item, old) for old in [*history, *selected]):
            continue
        selected.append(item)
        publishers.add(publisher)
    return selected
