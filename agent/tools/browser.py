# agent/tools/browser.py
"""
Browser tool — fetch web pages and run lightweight searches.

Natural-language triggers
--------------------------
  "open https://example.com"
  "browse to https://…"
  "fetch the page at https://…"
  "search for Python asyncio tutorial"
  "google how to reverse a list"

The tool performs an HTTP GET (no JavaScript rendering) and returns a
plain-text excerpt of the page or search results.

No external dependencies beyond the standard library are required for
the basic fetch path.  ``requests`` is used when available for richer
header support; otherwise ``urllib`` is the fallback.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "browser"

_BROWSE_KEYWORDS = re.compile(
    r"\b(open|browse|fetch|visit|go to|load|get|scrape|read|look up|check out)\b.*https?://|"
    r"https?://\S+|"
    r"\b(search for|google|look up|find info about|what is|tell me about)\b",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# Maximum characters returned to the agent
_MAX_CONTENT = 2000
# Request timeout (seconds)
_TIMEOUT = 10


def is_tool_query(message: str) -> bool:
    """Return True if the message appears to be a browser/search request."""
    return bool(_BROWSE_KEYWORDS.search(message))


def _extract_url(message: str) -> Optional[str]:
    m = _URL_RE.search(message)
    return m.group(0).rstrip(".,)") if m else None


def _extract_search_query(message: str) -> Optional[str]:
    """Extract a plain search term when no URL is present."""
    for prefix in (
        "search for",
        "google",
        "look up",
        "find info about",
        "what is",
        "tell me about",
    ):
        m = re.search(
            r"\b" + re.escape(prefix) + r"\s+(.+)",
            message,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).strip().strip("?.,")
    return None


def _strip_html(raw: str) -> str:
    """Very lightweight HTML → plain-text conversion (no extra deps)."""
    # Remove <script> and <style> blocks entirely
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    # Replace block-level tags with newlines
    raw = re.sub(r"</?(p|br|div|h[1-6]|li|tr|th|td)\b[^>]*>", "\n", raw, flags=re.I)
    # Strip remaining tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    # Collapse whitespace
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _fetch_url(url: str) -> str:
    """Fetch *url* and return a plain-text excerpt."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; CurieAI/1.0; +https://github.com/yessur3808/curie-ai)"
        )
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            charset = "utf-8"
            ct = resp.headers.get_content_charset()
            if ct:
                charset = ct
            raw = resp.read(1_000_000).decode(charset, errors="replace")
        text = _strip_html(raw)
        if len(text) > _MAX_CONTENT:
            text = text[:_MAX_CONTENT] + "\n…[truncated]"
        return text
    except urllib.error.HTTPError as exc:
        return f"[HTTP {exc.code}] {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return f"[Fetch error] {exc}"


def _build_search_url(query: str) -> str:
    """Build a DuckDuckGo search URL for *query*."""
    from urllib.parse import quote_plus  # noqa: PLC0415

    return f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"


async def handle_tool_query(
    message: str,
    **_kwargs,
) -> Optional[str]:
    """
    Handle a browser/search query.

    Returns a formatted response string, or None if the message is not
    a browser request.
    """
    if not is_tool_query(message):
        return None

    url = _extract_url(message)

    if url is None:
        query = _extract_search_query(message)
        if not query:
            return None
        url = _build_search_url(query)
        label = f"Search results for: *{query}*"
    else:
        label = f"Page content from: {url}"

    import asyncio  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    content = await loop.run_in_executor(None, _fetch_url, url)

    if not content:
        return f"🌐 {label}\n\n_(no readable content found)_"

    return f"🌐 {label}\n\n{content}"
