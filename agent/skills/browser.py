# agent/skills/browser.py
"""
Browser skill — agent-driven web-page fetching and interaction.

Provides the agent with the ability to:
  * Fetch a URL and return its readable text content
  * Extract all links from a page
  * Screenshot a page as structured text (headings, paragraphs, links)
  * Submit simple HTML forms (GET/POST)

Security:
  * All URLs are validated through the same ``is_safe_url`` SSRF guard that
    ``find_info`` uses to prevent server-side request forgery.
  * Follows at most ``MAX_REDIRECTS`` hops.
  * Rejects non-http/https schemes, private/loopback IPs, cloud metadata
    endpoints, and suspicious ports.

Dependencies:
  * ``httpx`` (already a project dependency)
  * ``beautifulsoup4`` (already a project dependency)

Usage::

    from agent.skills.browser import fetch_page, extract_links, page_screenshot

    result = await fetch_page("https://example.com")
    links  = await extract_links("https://example.com")
    snap   = await page_screenshot("https://example.com")
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

# Reuse the battle-tested SSRF guard from find_info
from agent.skills.find_info import is_safe_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_REDIRECTS: int = 5
CONNECT_TIMEOUT: float = float(os.getenv("BROWSER_CONNECT_TIMEOUT", "5"))
READ_TIMEOUT: float = float(os.getenv("BROWSER_READ_TIMEOUT", "15"))
MAX_CONTENT_CHARS: int = int(os.getenv("BROWSER_MAX_CONTENT_CHARS", "8000"))
MAX_LINKS: int = int(os.getenv("BROWSER_MAX_LINKS", "50"))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CurieBot/1.0; +https://github.com/yessur3808/curie-ai)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_readable_text(soup: BeautifulSoup) -> str:
    """Extract clean readable text from a parsed HTML document."""
    # Remove non-content tags
    for tag in soup(["script", "style", "noscript", "head", "nav", "footer", "aside"]):
        tag.decompose()

    lines: List[str] = []

    # Headings first (most important structural content)
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = heading.get_text(separator=" ", strip=True)
        if text:
            level = int(heading.name[1])
            prefix = "#" * level
            lines.append(f"{prefix} {text}")

    # Paragraphs and list items
    for tag in soup.find_all(["p", "li", "blockquote", "td", "th"]):
        text = tag.get_text(separator=" ", strip=True)
        if text and len(text) > 20:  # skip tiny snippets
            lines.append(text)

    combined = "\n".join(lines)
    # Deduplicate consecutive blank lines
    import re

    combined = re.sub(r"\n{3,}", "\n\n", combined)
    return combined[:MAX_CONTENT_CHARS]


def _extract_links_from_soup(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """Return a list of {url, text} dicts for all anchor tags."""
    seen: set = set()
    links: List[Dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.scheme not in ("http", "https"):
            continue
        if full_url in seen:
            continue
        seen.add(full_url)
        text = a.get_text(separator=" ", strip=True) or full_url
        links.append({"url": full_url, "text": text[:120]})
        if len(links) >= MAX_LINKS:
            break
    return links


async def _fetch_raw(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Fetch *url* (with redirect following and SSRF guard).

    Returns:
        ``(html_content, final_url, error_message)``
        On success ``error_message`` is ``None``; on failure the first two are ``None``.
    """
    if not await is_safe_url(url):
        return None, None, f"URL blocked by SSRF guard: {url}"

    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=5, pool=5)
    redirect_count = 0
    current_url = url

    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, headers=_HEADERS
    ) as client:
        while redirect_count <= MAX_REDIRECTS:
            try:
                response = await client.get(current_url)
            except httpx.TimeoutException:
                return None, None, f"Request timed out fetching {current_url}"
            except httpx.RequestError as exc:
                return None, None, f"Network error: {exc}"

            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    return None, None, "Redirect with no Location header"
                next_url = urljoin(current_url, location)
                if not await is_safe_url(next_url):
                    return None, None, f"Redirect to blocked URL: {next_url}"
                current_url = next_url
                redirect_count += 1
                continue

            if response.status_code >= 400:
                return None, None, f"HTTP {response.status_code} from {current_url}"

            content_type = response.headers.get("content-type", "")
            if "text" not in content_type and "html" not in content_type:
                return None, None, f"Non-text content-type: {content_type}"

            return response.text, current_url, None

    return None, None, f"Too many redirects (>{MAX_REDIRECTS})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def fetch_page(url: str) -> Dict[str, Any]:
    """
    Fetch *url* and return its readable text content.

    Returns a dict with keys:
        - ``url`` — the final URL after redirects
        - ``title`` — page title (or ``""`` if missing)
        - ``content`` — readable text (headings + paragraphs, truncated)
        - ``error`` — ``None`` on success, error string otherwise
    """
    html, final_url, error = await _fetch_raw(url)
    if error:
        return {"url": url, "title": "", "content": "", "error": error}

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    content = _extract_readable_text(soup)

    return {
        "url": final_url,
        "title": title,
        "content": content,
        "error": None,
    }


async def extract_links(url: str) -> Dict[str, Any]:
    """
    Fetch *url* and return all hyperlinks found on the page.

    Returns a dict with keys:
        - ``url`` — the final URL after redirects
        - ``links`` — list of ``{"url": str, "text": str}`` dicts
        - ``error`` — ``None`` on success, error string otherwise
    """
    html, final_url, error = await _fetch_raw(url)
    if error:
        return {"url": url, "links": [], "error": error}

    soup = BeautifulSoup(html, "html.parser")
    links = _extract_links_from_soup(soup, final_url)

    return {
        "url": final_url,
        "links": links,
        "error": None,
    }


async def page_screenshot(url: str) -> Dict[str, Any]:
    """
    Return a structured text "screenshot" of *url*.

    The snapshot captures the page title, headings, main text, and a
    compact link list — suitable as context for an LLM without requiring
    a real browser or image model.

    Returns a dict with keys:
        - ``url`` — the final URL after redirects
        - ``title`` — page title
        - ``headings`` — list of heading strings
        - ``summary`` — readable text content (truncated)
        - ``links`` — first 10 links
        - ``error`` — ``None`` on success, error string otherwise
    """
    html, final_url, error = await _fetch_raw(url)
    if error:
        return {
            "url": url,
            "title": "",
            "headings": [],
            "summary": "",
            "links": [],
            "error": error,
        }

    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    # Headings
    headings = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = h.get_text(separator=" ", strip=True)
        if text:
            headings.append(text)

    # Main content
    summary = _extract_readable_text(soup)

    # Links (first 10 only for snapshot)
    all_links = _extract_links_from_soup(soup, final_url)

    return {
        "url": final_url,
        "title": title,
        "headings": headings,
        "summary": summary,
        "links": all_links[:10],
        "error": None,
    }


async def submit_form(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Submit a simple HTML form to *url*.

    Args:
        url:    Target URL.
        method: HTTP method (``"GET"`` or ``"POST"``).
        data:   Form field key/value pairs.

    Returns:
        Same structure as :func:`fetch_page`.
    """
    if not await is_safe_url(url):
        return {"url": url, "title": "", "content": "", "error": f"URL blocked: {url}"}

    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=5, pool=5)
    method_upper = (method or "GET").upper()
    form_data = data or {}

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=timeout,
            headers=_HEADERS,
        ) as client:
            if method_upper == "POST":
                response = await client.post(url, data=form_data)
            else:
                response = await client.get(url, params=form_data)

            if response.status_code >= 400:
                return {
                    "url": str(response.url),
                    "title": "",
                    "content": "",
                    "error": f"HTTP {response.status_code}",
                }

            soup = BeautifulSoup(response.text, "html.parser")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            content = _extract_readable_text(soup)

            return {
                "url": str(response.url),
                "title": title,
                "content": content,
                "error": None,
            }

    except httpx.TimeoutException:
        return {"url": url, "title": "", "content": "", "error": "Request timed out"}
    except httpx.RequestError as exc:
        return {"url": url, "title": "", "content": "", "error": f"Network error: {exc}"}


# ---------------------------------------------------------------------------
# Intent detection helper (used by ChatWorkflow skill routing)
# ---------------------------------------------------------------------------

_BROWSER_KEYWORDS = [
    "open ",
    "browse to",
    "go to ",
    "visit ",
    "navigate to",
    "fetch ",
    "load page",
    "show page",
    "take a snapshot of",
    "screenshot of",
    "links on",
    "links from",
]


def is_browser_intent(text: str) -> bool:
    """Return True if *text* looks like a browser/navigation request."""
    lower = text.lower()
    return any(kw in lower for kw in _BROWSER_KEYWORDS) or (
        ("http://" in lower or "https://" in lower)
        and any(w in lower for w in ["show", "open", "fetch", "get", "read", "visit"])
    )


async def handle_browser_query(
    text: str,
    *_args: Any,
    **_kwargs: Any,
) -> Optional[str]:
    """
    Entry point for the browser skill when called from ChatWorkflow.

    Extracts a URL from *text*, fetches the page, and returns a
    human-readable summary suitable for sending back to the user.
    Returns ``None`` if no URL is found in the text.
    """
    import re

    url_match = re.search(r"https?://[^\s\"'<>]+", text)
    if not url_match:
        return None

    url = url_match.group(0).rstrip(".,;:!?)")

    # Decide what action to take based on keywords
    lower = text.lower()
    if any(w in lower for w in ["links", "extract links", "list links"]):
        result = await extract_links(url)
        if result["error"]:
            return f"⚠️ Could not extract links: {result['error']}"
        if not result["links"]:
            return f"🔗 No links found on {result['url']}"
        link_lines = [f"• [{lnk['text'][:60]}]({lnk['url']})" for lnk in result["links"][:15]]
        return f"🔗 **Links on {result['url']}**\n\n" + "\n".join(link_lines)

    if any(w in lower for w in ["screenshot", "snapshot", "structure"]):
        result = await page_screenshot(url)
        if result["error"]:
            return f"⚠️ Could not snapshot page: {result['error']}"
        parts = [f"📸 **{result['title'] or result['url']}**"]
        if result["headings"]:
            parts.append("\n**Headings:**\n" + "\n".join(f"• {h}" for h in result["headings"][:6]))
        if result["summary"]:
            parts.append("\n**Content:**\n" + result["summary"][:1500])
        return "\n".join(parts)

    # Default: fetch page
    result = await fetch_page(url)
    if result["error"]:
        return f"⚠️ Could not fetch page: {result['error']}"

    title = result["title"] or result["url"]
    content = result["content"]
    if not content:
        return f"📄 **{title}**\n\n_(Page appears to be empty or non-text)_"

    return f"📄 **{title}**\n\n{content[:2000]}"
