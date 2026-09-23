#!/usr/bin/env python3
"""
Tests for the message formatting utilities (utils/formatting.py).
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.formatting import (
    plain_links,
    strip_markdown,
    format_for_platform,
    MARKDOWN_SKILL_MODELS,
    normalize_markdown_layout,
    rich_format_preview_request,
    telegram_html,
)  # noqa: E402


def test_inline_bold_label_list_is_restored_without_damaging_italics():
    source = (
        "Three ideas: * **Fresh air**: Open a window. *Un peu d'air frais*. "
        "* **Warm drink**: Make tea. * **Read**: Pick a book."
    )

    normalized = normalize_markdown_layout(source)
    rendered = telegram_html(source)

    assert normalized.count("\n- **") == 3
    assert "*Un peu d'air frais*" in normalized
    assert rendered.count("\n• <b>") == 3
    assert "<i>Un peu d&#x27;air frais</i>" in rendered


def test_telegram_html_supports_rich_text_and_safe_links():
    rendered = telegram_html(
        "# Status\n**Bold** *italic* ~~old~~ ++underlined++ `code` "
        "[Open](https://example.com?a=1&b=2)\n- First"
    )

    assert "<b>Status</b>" in rendered
    assert "<b>Bold</b>" in rendered
    assert "<i>italic</i>" in rendered
    assert "<s>old</s>" in rendered
    assert "<u>underlined</u>" in rendered
    assert "<code>code</code>" in rendered
    assert '<a href="https://example.com?a=1&amp;b=2">Open</a>' in rendered
    assert "• First" in rendered


def test_telegram_html_renders_markdown_table_as_preformatted_text():
    rendered = telegram_html("| Device | State |\n| --- | --- |\n| Lamp | Off |")

    assert rendered.startswith("<pre>")
    assert "Device | State" in rendered
    assert "Lamp" in rendered


def test_telegram_html_escapes_untrusted_html_and_unsafe_links():
    rendered = telegram_html("<script>x</script> [bad](javascript:alert(1))")

    assert "&lt;script&gt;" in rendered
    assert "<script>" not in rendered
    assert "<a href=" not in rendered


def test_explicit_rich_format_preview_exercises_every_supported_style():
    preview = rich_format_preview_request(
        "Formatting test: show bold, italic, strike, underline, table, list, and link"
    )

    assert preview is not None
    rendered = telegram_html(preview)
    assert "<b>Curie formatting check</b>" in rendered
    assert "• <b>Status:</b> Ready" in rendered
    assert "<i>Italic sample.</i>" in rendered
    assert "<s>Old wording</s>" in rendered
    assert "<u>Underlined note</u>" in rendered
    assert "<pre>" in rendered
    assert '<a href="https://example.com">Example</a>' in rendered


def test_normal_message_does_not_trigger_format_preview():
    assert rich_format_preview_request("Please format this update as bullets") is None


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
        assert (
            result == "Google Maps: https://g.co and Apple Maps: https://maps.apple.com"
        )

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
        text = (
            "[Bing Maps](https://bing.com/maps/default.aspx?rtp=pos.1_2~pos.3_4&mode=D)"
        )
        result = plain_links(text)
        assert (
            result
            == "Bing Maps: https://bing.com/maps/default.aspx?rtp=pos.1_2~pos.3_4&mode=D"
        )

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
        assert "_Wednesday_" not in result
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
        assert (
            format_for_platform(self._SAMPLE_MARKDOWN, "telegram")
            == self._SAMPLE_MARKDOWN
        )

    def test_discord_unchanged(self):
        assert (
            format_for_platform(self._SAMPLE_MARKDOWN, "discord")
            == self._SAMPLE_MARKDOWN
        )

    def test_api_unchanged(self):
        assert (
            format_for_platform(self._SAMPLE_MARKDOWN, "api") == self._SAMPLE_MARKDOWN
        )

    def test_websocket_unchanged(self):
        assert (
            format_for_platform(self._SAMPLE_MARKDOWN, "websocket")
            == self._SAMPLE_MARKDOWN
        )

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
