# tests/test_datetime_info.py

import sys
import os
from datetime import datetime, timezone
import pytest

# Add parent directory to path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.datetime_info import (
    get_current_datetime,
    extract_timezone_from_message,
    extract_city_from_message
)


def test_get_current_datetime_utc():
    """Test getting current datetime in UTC"""
    result = get_current_datetime("UTC")
    assert "Current date:" in result
    assert "Current time:" in result
    assert "UTC" in result


def test_get_current_datetime_with_timezone():
    """Test getting current datetime with specific timezone"""
    result = get_current_datetime("America/New_York")
    assert "Current date:" in result
    assert "Current time:" in result
    assert "EST" in result or "EDT" in result  # Depends on DST


def test_get_current_datetime_invalid_timezone():
    """Test that invalid timezone falls back to UTC"""
    result = get_current_datetime("Invalid/Timezone")
    assert "UTC" in result


def test_extract_timezone_no_match():
    """Test timezone extraction returns None when no timezone found"""
    result = extract_timezone_from_message("What is the weather like?")
    assert result is None


def test_extract_timezone_city_names():
    """Test timezone extraction for common city names"""
    # Test major cities
    assert extract_timezone_from_message("What time is it in Tokyo?") == "Asia/Tokyo"
    assert extract_timezone_from_message("Current time in London") == "Europe/London"
    assert extract_timezone_from_message("Time in Paris") == "Europe/Paris"
    assert extract_timezone_from_message("What's the time in Hong Kong?") == "Asia/Hong_Kong"


def test_extract_timezone_abbreviations():
    """Test timezone extraction for common abbreviations"""
    # Test that full abbreviations work
    assert extract_timezone_from_message("What time is it in NYC?") == "America/New_York"
    assert extract_timezone_from_message("Time in SF") == "America/Los_Angeles"


def test_extract_timezone_false_positives():
    """Test that word boundary matching prevents false positives"""
    # "any" should NOT match "ny"
    result = extract_timezone_from_message("Is there any news?")
    assert result is None
    
    # "delay" should NOT match "la"
    result = extract_timezone_from_message("There was a delay")
    assert result is None
    
    # "analysis" should NOT match "ny"
    result = extract_timezone_from_message("Can you do an analysis?")
    assert result is None


def test_extract_timezone_case_insensitive():
    """Test that timezone extraction is case-insensitive"""
    assert extract_timezone_from_message("time in TOKYO") == "Asia/Tokyo"
    assert extract_timezone_from_message("time in london") == "Europe/London"


def test_extract_city_no_match():
    """Test city extraction returns None when no city found"""
    result = extract_city_from_message("What is the weather?")
    assert result is None


def test_extract_city_from_message():
    """Test city extraction from messages"""
    assert extract_city_from_message("What's the weather in Tokyo?") == "Tokyo"
    assert extract_city_from_message("Weather in Hong Kong") == "Hong Kong"
    assert extract_city_from_message("How's the weather in New York?") == "New York"
    assert extract_city_from_message("Tell me the weather in San Francisco") == "San Francisco"


def test_extract_city_case_insensitive():
    """Test that city extraction is case-insensitive"""
    assert extract_city_from_message("weather in TOKYO") == "Tokyo"
    assert extract_city_from_message("weather in london") == "London"


def test_extract_city_multi_word():
    """Test city extraction for multi-word city names"""
    assert extract_city_from_message("Weather in Los Angeles?") == "Los Angeles"
    assert extract_city_from_message("What's the weather in New York City?") == "New York"
    assert extract_city_from_message("How is Hong Kong today?") == "Hong Kong"


def test_timezone_and_city_extraction_together():
    """Test that both timezone and city can be extracted from same message"""
    message = "What's the weather in Hong Kong?"
    tz = extract_timezone_from_message(message)
    city = extract_city_from_message(message)
    assert tz == "Asia/Hong_Kong"
    assert city == "Hong Kong"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
