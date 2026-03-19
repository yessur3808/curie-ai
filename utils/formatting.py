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


def plain_links(text: str) -> str:
    """
    Convert Markdown-style hyperlinks to plain-text format.

    Transforms ``[Link Name](https://example.com)`` into
    ``Link Name: https://example.com`` so the URL is visible and
    auto-linked on platforms that do not support Markdown hyperlinks
    (e.g. WhatsApp).

    Note: The regex matches the first ``]`` and ``)``, so link names
    containing ``]`` or URLs containing unbalanced ``(``/``)`` may not
    be handled correctly.  All URLs produced by ``generate_map_links``
    are well-formed and do not contain bare parentheses, so this
    limitation does not affect the navigation skill in practice.

    Args:
        text: Input text that may contain ``[name](url)`` patterns.

    Returns:
        Text with all Markdown link patterns replaced by ``name: url``.
    """
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1: \2", text)


def format_for_platform(text: str, platform: str) -> str:
    """
    Apply any platform-specific formatting adjustments to a response string.

    For platforms that do not support Markdown hyperlinks the
    ``[name](url)`` patterns are expanded to ``name: url`` so the link
    is still useful.

    Args:
        text:     The response text to format.
        platform: The target platform identifier (e.g. ``'telegram'``,
                  ``'whatsapp'``, ``'discord'``).

    Returns:
        The appropriately formatted response string.
    """
    if platform not in _MARKDOWN_LINK_PLATFORMS:
        return plain_links(text)
    return text
