# utils/formatting.py
"""
Message formatting utilities for multi-platform output.

Different chat platforms render text differently:
  - Telegram: supports Markdown (*bold*, _italic_, [text](url)) when
    parse_mode="Markdown" is set on the message.
  - Discord: renders [text](url) hyperlinks natively.
  - WhatsApp: does NOT support [text](url) hyperlinks; raw URLs are
    auto-linked but the markdown wrapper is shown as literal characters.
  - API / WebSocket: returns raw text; clients decide how to render it.
"""

import html
import re
from urllib.parse import urlparse

# Platforms that natively render Markdown [text](url) links.
_MARKDOWN_LINK_PLATFORMS = {"telegram", "discord", "api", "websocket"}

# Skills whose responses contain Markdown formatting that should be rendered.
# Used by connectors to set parse_mode / equivalent.
MARKDOWN_SKILL_MODELS = frozenset(
    {
        "navigation_skill",
        "scheduler_skill",
        "trip_planner_skill",
    }
)


def rich_format_preview_request(text: str) -> str | None:
    """Return a stable rich-text preview for an explicit formatting test."""
    if not re.search(
        r"\b(?:formatting\s+test|(?:show|demonstrate|test).{0,40}"
        r"(?:telegram|message|rich[- ]?text)\s+formatting)\b",
        str(text or ""),
        re.I | re.S,
    ):
        return None
    return (
        "**Curie formatting check**\n\n"
        "- **Status:** Ready\n"
        "- *Style:* Compact and readable\n\n"
        "| Feature | Result |\n"
        "| --- | --- |\n"
        "| Lists | Ready |\n"
        "| Links | Ready |\n\n"
        "*Italic sample.* ~~Old wording~~ ++Underlined note++ "
        "[Example](https://example.com)"
    )


def _safe_telegram_link(url: str) -> bool:
    parsed = urlparse(html.unescape(url).strip())
    return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.netloc)


def _render_markdown_table(lines: list[str]) -> str | None:
    """Render a small Markdown table as an aligned Telegram ``pre`` block."""
    if len(lines) < 2:
        return None
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines
    ]
    if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in rows[1]):
        return None
    rows = [rows[0], *rows[2:]]
    columns = max((len(row) for row in rows), default=0)
    if not columns:
        return None
    normalized = [row + [""] * (columns - len(row)) for row in rows]
    widths = [
        min(24, max(len(row[index]) for row in normalized)) for index in range(columns)
    ]
    rendered = []
    for row_index, row in enumerate(normalized):
        rendered.append(
            " | ".join(
                value[: widths[index]].ljust(widths[index])
                for index, value in enumerate(row)
            ).rstrip()
        )
        if row_index == 0:
            rendered.append("-+-".join("-" * width for width in widths))
    return "<pre>" + html.escape("\n".join(rendered)) + "</pre>"


def telegram_html(text: str) -> str:
    """Convert conservative Markdown into safe Telegram Bot API HTML.

    Telegram HTML supports bold, italic, underline, strikethrough, code,
    links, block quotes, and preformatted blocks. All other HTML is escaped.
    ``++text++`` is Curie's portable source syntax for underline.
    """
    source = str(text or "").replace("\r\n", "\n")
    protected: list[str] = []

    def token(rendered: str) -> str:
        marker = f"\x00TG{len(protected)}\x00"
        protected.append(rendered)
        return marker

    def code_block(match: re.Match) -> str:
        language = (match.group(1) or "").strip()
        body = match.group(2).strip("\n")
        language_attr = (
            f' class="language-{html.escape(language, quote=True)}"'
            if re.fullmatch(r"[A-Za-z0-9_+.-]{1,30}", language)
            else ""
        )
        return token(f"<pre><code{language_attr}>{html.escape(body)}</code></pre>")

    source = re.sub(r"```([^\n`]*)\n([\s\S]*?)```", code_block, source)

    lines = source.split("\n")
    rebuilt: list[str] = []
    index = 0
    while index < len(lines):
        if "|" in lines[index] and index + 1 < len(lines):
            end = index + 1
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            table = _render_markdown_table(lines[index:end])
            if table:
                rebuilt.append(token(table))
                index = end
                continue
        rebuilt.append(lines[index])
        index += 1
    source = "\n".join(rebuilt)

    def link(match: re.Match) -> str:
        label, url = match.group(1), html.unescape(match.group(2)).strip()
        if not _safe_telegram_link(url):
            return f"{label} ({url})"
        return token(
            f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
        )

    source = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, source)
    source = re.sub(
        r"`([^`\n]+)`",
        lambda match: token(f"<code>{html.escape(match.group(1))}</code>"),
        source,
    )
    source = html.escape(source)

    source = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", source, flags=re.MULTILINE)
    source = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", source)
    source = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", source)
    source = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", source)
    source = re.sub(r"\+\+([^+\n]+)\+\+", r"<u>\1</u>", source)
    source = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", source)
    source = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<i>\1</i>", source)
    source = re.sub(r"^\s*[-*]\s+", "• ", source, flags=re.MULTILINE)
    source = re.sub(
        r"^&gt;\s?(.+)$", r"<blockquote>\1</blockquote>", source, flags=re.MULTILINE
    )

    for index, rendered in enumerate(protected):
        source = source.replace(f"\x00TG{index}\x00", rendered)
    return source.strip()


def plain_links(text: str) -> str:
    """
    Convert Markdown-style hyperlinks to plain-text format.

    Transforms ``[Link Name](https://example.com)`` into
    ``Link Name: https://example.com`` so the URL is visible and
    auto-linked on platforms that do not support Markdown hyperlinks
    (e.g. WhatsApp).
    """
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1: \2", text)


def strip_markdown(text: str) -> str:
    """
    Remove common Markdown formatting characters so the text renders
    cleanly on platforms that display them as literal symbols (e.g. WhatsApp).

    Handles:
      - **bold** / __bold__
      - *italic* / _italic_
      - ~~strikethrough~~
      - `inline code`
      - # Headings
      - [text](url) → text: url  (via plain_links)
    """
    # Headings: "# Title" → "Title"
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold: **text** or __text__ (single-line, non-greedy)
    text = re.sub(
        r"\*\*([^*\n]+)\*\*|__([^_\n]+)__",
        lambda m: m.group(1) if m.group(1) is not None else m.group(2),
        text,
    )
    # Italic: *text* (not part of **bold**) using word-boundary guards
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)", r"\1", text)
    # Italic: _text_ — only match underscores surrounded by whitespace or start/end of string
    # to avoid stripping underscores in identifiers like user_message
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"\1", text)
    # Strikethrough: ~~text~~
    text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
    # Portable underline syntax used by Telegram's rich renderer.
    text = re.sub(r"\+\+([^+\n]+)\+\+", r"\1", text)
    # Inline code: `code`
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Markdown links
    text = plain_links(text)
    return text


def escape_markdown(text: str) -> str:
    """Escape Telegram Markdown v1 special characters in user-supplied text.

    When skill responses embed user-controlled strings inside Markdown markers
    (e.g. ``**{user_text}**``), special characters in that text can break
    Telegram's Markdown parser and cause ``BadRequest: can't parse entities``
    errors.  This helper escapes the characters that Telegram v1 treats as
    formatting: ``_``, ``*``, `` ` ``, and ``[``.

    Use this on *user-derived* content before embedding it in formatted strings
    that will be sent via ``parse_mode="Markdown"``.
    """
    # Telegram Markdown v1 special characters (in order of most common)
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_for_platform(text: str, platform: str) -> str:
    """
    Apply any platform-specific formatting adjustments to a response string.

    For platforms that do not support Markdown the formatting characters are
    stripped so responses look clean rather than showing ``**bold**`` literally.

    Args:
        text:     The response text to format.
        platform: The target platform identifier (e.g. ``'telegram'``,
                  ``'whatsapp'``, ``'discord'``).

    Returns:
        The appropriately formatted response string.
    """
    if platform in _MARKDOWN_LINK_PLATFORMS:
        return text
    # For non-Markdown platforms (e.g. WhatsApp): strip formatting characters
    return strip_markdown(text)
