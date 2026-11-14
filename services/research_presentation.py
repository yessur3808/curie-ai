"""Keep research readable while retaining owner-scoped source references."""

import re

from memory.local_store import list_personal_items, save_personal_item

_KIND = "research_source_receipt"
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"https?://[^\s<>]+")
_REQUEST = re.compile(
    r"^(?:/sources|sources?|(?:show|send|give)(?: me)? (?:the )?(?:sources?|links?|references?)"
    r"(?: for (?:that|this|it))?|where did you (?:find|get|read) (?:that|this|it))\s*[?.!]*$",
    re.I,
)


def compact_research(owner: str, message: str, *, remember: bool = True) -> str:
    """Remove citation URLs from a research delivery, retaining them for recall."""
    links = []

    def replace_link(match):
        links.append({"label": match[1], "url": match[2]})
        return match[1]

    text = _LINK.sub(replace_link, message)
    for match in _URL.finditer(text):
        links.append({"label": "Source", "url": match[0].rstrip(".,;)")})
    if not links:
        return message
    unique = {item["url"]: item for item in links}
    if remember:
        save_personal_item(
            str(owner),
            _KIND,
            {"id": "latest-research-sources", "sources": list(unique.values())[:20]},
        )
    text = re.split(r"\n\s*(?:\*\*)?Sources:(?:\*\*)?\s*\n", text, maxsplit=1)[0]
    text = _URL.sub("", text)
    text = re.sub(r"\[S\d+\]", "", text)
    text = re.sub(r"(?m)^\s*(?:Also reported|Spotted on X):\s*$", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n\nSources available—ask me for them."


def requested_sources(owner: str, text: str) -> str | None:
    if not _REQUEST.fullmatch(text.strip()):
        return None
    rows = list_personal_items(str(owner), _KIND)
    receipt = rows[0] if rows else None
    if not receipt:
        return "I don’t have saved source links for a recent research update yet."
    return "Sources for my latest research update:\n\n" + "\n".join(
        f"{index}. {item['label']}: {item['url']}"
        for index, item in enumerate(receipt["sources"], 1)
    )
