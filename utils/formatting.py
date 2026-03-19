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

import re

# Platforms that natively render Markdown [text](url) links.
_MARKDOWN_LINK_PLATFORMS = {"telegram", "discord", "api", "websocket"}

# Skills whose responses contain Markdown formatting that should be rendered.
# Used by connectors to set parse_mode / equivalent.
MARKDOWN_SKILL_MODELS = frozenset({
    "navigation_skill",
    "scheduler_skill",
    "trip_planner_skill",
})


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
    # Inline code: `code`
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Markdown links
    text = plain_links(text)
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
