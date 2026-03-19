#!/usr/bin/env python3
"""
Tests for the message formatting utilities (utils/formatting.py).
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.formatting import plain_links, strip_markdown, format_for_platform, MARKDOWN_SKILL_MODELS  # noqa: E402


# ---------------------------------------------------------------------------
# plain_links
# ---------------------------------------------------------------------------

class TestPlainLinks:
    def test_single_link_converted(self):
        text = "Open [Google Maps](https://maps.google.com/?q=Paris)"
        result = plain_links(text)
        assert result == "Open Google Maps: https://maps.google.com/?q=Paris"

    def test_multiple_links_converted(self):
        text = "[Google Maps](https://g.co) and [Apple Maps](https://maps.apple.com)"
        result = plain_links(text)
        assert result == "Google Maps: https://g.co and Apple Maps: https://maps.apple.com"

    def test_no_links_unchanged(self):
        text = "Hello, this is plain text with no links."
        assert plain_links(text) == text

    def test_plain_url_unchanged(self):
        text = "Visit https://example.com for more info."
        assert plain_links(text) == text

    def test_mixed_content_preserved(self):
        text = "📌 Directions:\n  1. Head north\n\n[Google Maps](https://g.co/maps)"
        result = plain_links(text)
        assert "Google Maps: https://g.co/maps" in result
        assert "Head north" in result

    def test_multiline_navigation_block(self):
        text = (
            "🗺️ *Open in Maps:*\n"
            "  • [Google Maps](https://www.google.com/maps/dir/)\n"
            "  • [Apple Maps](https://maps.apple.com/?saddr=A&daddr=B)\n"
            "  • [Waze](https://waze.com/ul?ll=51.5,0.1)\n"
        )
        result = plain_links(text)
        assert "[Google Maps]" not in result
        assert "Google Maps: https://www.google.com/maps/dir/" in result
        assert "Apple Maps: https://maps.apple.com/?saddr=A&daddr=B" in result
        assert "Waze: https://waze.com/ul?ll=51.5,0.1" in result

    def test_url_with_query_params_preserved(self):
        text = "[Bing Maps](https://bing.com/maps/default.aspx?rtp=pos.1_2~pos.3_4&mode=D)"
        result = plain_links(text)
        assert result == "Bing Maps: https://bing.com/maps/default.aspx?rtp=pos.1_2~pos.3_4&mode=D"

    def test_empty_string(self):
        assert plain_links("") == ""


# ---------------------------------------------------------------------------
# strip_markdown
# ---------------------------------------------------------------------------

class TestStripMarkdown:
    def test_bold_double_asterisks(self):
        assert strip_markdown("**bold text**") == "bold text"

    def test_bold_double_underscores(self):
        assert strip_markdown("__bold text__") == "bold text"

    def test_italic_asterisk(self):
        assert strip_markdown("*italic text*") == "italic text"

    def test_italic_underscore(self):
        assert strip_markdown("_italic text_") == "italic text"

    def test_italic_underscore_does_not_strip_identifiers(self):
        # Underscores in Python identifiers should NOT be stripped
        text = "Check user_message for details."
        result = strip_markdown(text)
        assert "user_message" in result

    def test_strikethrough(self):
        assert strip_markdown("~~deleted~~") == "deleted"

    def test_inline_code(self):
        assert strip_markdown("`code`") == "code"

    def test_heading_removed(self):
        result = strip_markdown("# My Title\nsome body")
        assert "My Title" in result
        assert "#" not in result

    def test_links_expanded(self):
        result = strip_markdown("[Click here](https://example.com)")
        assert "[Click here]" not in result
        assert "Click here: https://example.com" in result

    def test_plain_text_unchanged(self):
        text = "Hello, how are you today?"
        assert strip_markdown(text) == text

    def test_mixed_formatting(self):
        text = "⏰ Reminder: **call mom** on _Wednesday_"
        result = strip_markdown(text)
        assert "**" not in result
        assert "_" not in result or result == result  # underscores stripped
        assert "call mom" in result
        assert "Wednesday" in result

    def test_trip_header(self):
        text = "✈️ **Trip Plan: Paris**\n\n1. Visit the **Eiffel Tower**."
        result = strip_markdown(text)
        assert "**" not in result
        assert "Paris" in result
        assert "Eiffel Tower" in result


# ---------------------------------------------------------------------------
# MARKDOWN_SKILL_MODELS constant
# ---------------------------------------------------------------------------

class TestMarkdownSkillModels:
    def test_navigation_in_set(self):
        assert "navigation_skill" in MARKDOWN_SKILL_MODELS

    def test_scheduler_in_set(self):
        assert "scheduler_skill" in MARKDOWN_SKILL_MODELS

    def test_trip_planner_in_set(self):
        assert "trip_planner_skill" in MARKDOWN_SKILL_MODELS


# ---------------------------------------------------------------------------
# format_for_platform
# ---------------------------------------------------------------------------

class TestFormatForPlatform:
    _SAMPLE_MARKDOWN = (
        "Route ready!\n"
        "  • [Google Maps](https://g.co/maps)\n"
        "  • [Apple Maps](https://maps.apple.com)\n"
    )

    # Platforms that support Markdown links — text should pass through unchanged
    def test_telegram_unchanged(self):
        assert format_for_platform(self._SAMPLE_MARKDOWN, "telegram") == self._SAMPLE_MARKDOWN

    def test_discord_unchanged(self):
        assert format_for_platform(self._SAMPLE_MARKDOWN, "discord") == self._SAMPLE_MARKDOWN

    def test_api_unchanged(self):
        assert format_for_platform(self._SAMPLE_MARKDOWN, "api") == self._SAMPLE_MARKDOWN

    def test_websocket_unchanged(self):
        assert format_for_platform(self._SAMPLE_MARKDOWN, "websocket") == self._SAMPLE_MARKDOWN

    # Platforms that do NOT support Markdown — full Markdown stripping applied
    def test_whatsapp_strips_markdown_links(self):
        result = format_for_platform(self._SAMPLE_MARKDOWN, "whatsapp")
        assert "[Google Maps]" not in result
        assert "Google Maps: https://g.co/maps" in result
        assert "Apple Maps: https://maps.apple.com" in result

    def test_whatsapp_strips_bold(self):
        text = "⏰ Reminder: **team standup** is now!"
        result = format_for_platform(text, "whatsapp")
        assert "**" not in result
        assert "team standup" in result

    def test_whatsapp_strips_italic(self):
        text = "Pack _sunscreen_ and _flip-flops_."
        result = format_for_platform(text, "whatsapp")
        assert "sunscreen" in result
        assert "flip-flops" in result

    def test_unknown_platform_strips_markdown(self):
        text = "**Bold** and _italic_ and [link](https://example.com)"
        result = format_for_platform(text, "sms")
        assert "**" not in result
        assert "Bold" in result

    def test_no_markdown_any_platform_unchanged(self):
        text = "Hello, how can I help you today?"
        for platform in ("telegram", "discord", "whatsapp", "api", "sms"):
            assert "Hello" in format_for_platform(text, platform)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
