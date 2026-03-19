#!/usr/bin/env python3
"""
Tests for the message formatting utilities (utils/formatting.py).
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.formatting import plain_links, format_for_platform  # noqa: E402


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
# format_for_platform
# ---------------------------------------------------------------------------

class TestFormatForPlatform:
    _SAMPLE = (
        "Route ready!\n"
        "  • [Google Maps](https://g.co/maps)\n"
        "  • [Apple Maps](https://maps.apple.com)\n"
    )

    # Platforms that support Markdown links — text should pass through unchanged
    def test_telegram_unchanged(self):
        assert format_for_platform(self._SAMPLE, "telegram") == self._SAMPLE

    def test_discord_unchanged(self):
        assert format_for_platform(self._SAMPLE, "discord") == self._SAMPLE

    def test_api_unchanged(self):
        assert format_for_platform(self._SAMPLE, "api") == self._SAMPLE

    def test_websocket_unchanged(self):
        assert format_for_platform(self._SAMPLE, "websocket") == self._SAMPLE

    # Platforms that do NOT support Markdown links — links should be expanded
    def test_whatsapp_converts_links(self):
        result = format_for_platform(self._SAMPLE, "whatsapp")
        assert "[Google Maps]" not in result
        assert "Google Maps: https://g.co/maps" in result
        assert "Apple Maps: https://maps.apple.com" in result

    def test_unknown_platform_converts_links(self):
        result = format_for_platform(self._SAMPLE, "sms")
        assert "[Google Maps]" not in result
        assert "Google Maps: https://g.co/maps" in result

    def test_no_links_any_platform_unchanged(self):
        text = "Hello, how can I help you today?"
        for platform in ("telegram", "discord", "whatsapp", "api", "sms"):
            assert format_for_platform(text, platform) == text


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
