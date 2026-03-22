# tests/test_datetime_info.py

import sys
import os
from datetime import datetime, timezone
import pytest

# Add parent directory to path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.datetime_info import (
    get_current_datetime,
    extract_timezone_from_message,
    extract_city_from_message,
)

# ---------------------------------------------------------------------------
# get_current_datetime — now returns a formatted string (not a dict)
# ---------------------------------------------------------------------------


def test_get_current_datetime_utc():
    """get_current_datetime should return a string containing date/time/timezone."""
    result = get_current_datetime("UTC")
    assert isinstance(result, str)
    assert "Current date:" in result
    assert "Current time:" in result
    assert "UTC" in result


def test_get_current_datetime_with_timezone():
    """A valid timezone should appear in the result."""
    result = get_current_datetime("America/New_York")
    assert isinstance(result, str)
    assert "Current date:" in result
    assert "Current time:" in result
    # EST or EDT depending on DST
    assert "EST" in result or "EDT" in result


def test_get_current_datetime_invalid_timezone():
    """An invalid timezone should fall back to UTC without raising."""
    result = get_current_datetime("Invalid/Timezone")
    assert isinstance(result, str)
    assert "UTC" in result


def test_get_current_datetime_contains_time_source():
    """Result should document the time source."""
    result = get_current_datetime("UTC")
    assert "Time source:" in result


def test_get_current_datetime_tokyo():
    """Tokyo timezone should appear in the result."""
    result = get_current_datetime("Asia/Tokyo")
    assert "JST" in result


# ---------------------------------------------------------------------------
# extract_timezone_from_message — returns None when no match
# ---------------------------------------------------------------------------


def test_extract_timezone_no_match():
    """Returns None when no recognisable location found."""
    result = extract_timezone_from_message("What is the weather like?")
    assert result is None


def test_extract_timezone_city_names():
    """Common city names map to correct timezone strings."""
    assert extract_timezone_from_message("What time is it in Tokyo?") == "Asia/Tokyo"
    assert extract_timezone_from_message("Current time in London") == "Europe/London"
    assert extract_timezone_from_message("Time in Paris") == "Europe/Paris"
    assert (
        extract_timezone_from_message("What's the time in Hong Kong?")
        == "Asia/Hong_Kong"
    )


def test_extract_timezone_abbreviations():
    """Common abbreviations should work."""
    assert (
        extract_timezone_from_message("What time is it in NYC?") == "America/New_York"
    )
    assert extract_timezone_from_message("Time in SF") == "America/Los_Angeles"


def test_extract_timezone_false_positives():
    """Word-boundary matching prevents false positives."""
    # "any" must NOT match "ny"
    assert extract_timezone_from_message("Is there any news?") is None
    # "delay" must NOT match "la"
    assert extract_timezone_from_message("There was a delay") is None
    # "analysis" must NOT match "ny"
    assert extract_timezone_from_message("Can you do an analysis?") is None


def test_extract_timezone_case_insensitive():
    """Timezone extraction is case-insensitive."""
    assert extract_timezone_from_message("time in TOKYO") == "Asia/Tokyo"
    assert extract_timezone_from_message("time in london") == "Europe/London"


# ---------------------------------------------------------------------------
# extract_city_from_message
# ---------------------------------------------------------------------------


def test_extract_city_no_match():
    """Returns None when no city found."""
    assert extract_city_from_message("What is the weather?") is None


def test_extract_city_from_message():
    """Common city names are returned in title case."""
    assert extract_city_from_message("What's the weather in Tokyo?") == "Tokyo"
    assert extract_city_from_message("Weather in Hong Kong") == "Hong Kong"
    assert extract_city_from_message("How's the weather in New York?") == "New York"
    assert (
        extract_city_from_message("Tell me the weather in San Francisco")
        == "San Francisco"
    )


def test_extract_city_case_insensitive():
    """City extraction is case-insensitive."""
    assert extract_city_from_message("weather in TOKYO") == "Tokyo"
    assert extract_city_from_message("weather in london") == "London"


def test_extract_city_multi_word():
    """Multi-word city names are handled correctly."""
    assert extract_city_from_message("Weather in Los Angeles?") == "Los Angeles"
    assert (
        extract_city_from_message("What's the weather in New York City?") == "New York"
    )
    assert extract_city_from_message("How is Hong Kong today?") == "Hong Kong"


def test_timezone_and_city_extraction_together():
    """Both timezone and city can be extracted from the same message."""
    message = "What's the weather in Hong Kong?"
    tz = extract_timezone_from_message(message)
    city = extract_city_from_message(message)
    assert tz == "Asia/Hong_Kong"
    assert city == "Hong Kong"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
