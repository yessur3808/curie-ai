# utils/datetime_info.py
"""
Date/time helpers used by the assistant.

All calls that need the current time go through ``get_current_datetime()``
which in turn calls ``utils.system_time.get_verified_now()``.  That function
uses the system clock as the authoritative source and optionally verifies it
against a public internet time API in the background (see system_time.py).
"""

import re
from datetime import datetime
from typing import Optional

import pytz


def get_current_datetime(timezone_str: str = "UTC") -> str:
    """Return a formatted string describing the current date and time.

    Uses the system clock (with optional background internet verification via
    ``utils.system_time``).

    Parameters
    ----------
    timezone_str : str
        A valid tz database timezone name, e.g. ``"UTC"``, ``"Asia/Tokyo"``.
        Falls back to UTC if the value is invalid.

    Returns
    -------
    str
        Multi-line string ready for injection into the assistant prompt, e.g.::

            Current date: Friday, March 20, 2026
            Current time: 05:21 AM UTC
            Timezone: UTC
            Time source: system clock (internet-verified)
    """
    try:
        tz = pytz.timezone(timezone_str)
    except (pytz.UnknownTimeZoneError, pytz.AmbiguousTimeError):
        tz = pytz.UTC
        timezone_str = "UTC"

    try:
        from utils.system_time import get_verified_now, get_time_source_label

        now: datetime = get_verified_now(tz=tz)
        source = get_time_source_label()
    except Exception:
        # Graceful fallback if system_time module is unavailable
        now = datetime.now(tz)
        source = "system clock"

    return (
        f"Current date: {now.strftime('%A, %B %d, %Y')}\n"
        f"Current time: {now.strftime('%I:%M %p %Z')}\n"
        f"Timezone: {timezone_str}\n"
        f"Time source: {source}"
    )


def extract_timezone_from_message(message: str) -> Optional[str]:
    """Extract a tz database timezone from a message based on location mentions.

    Uses word-boundary matching to avoid false positives
    (e.g. ``"any"`` does not match ``"ny"``).

    Parameters
    ----------
    message : str
        User message text.

    Returns
    -------
    str | None
        A tz database name such as ``"Asia/Tokyo"``, or ``None`` if no
        recognisable location was found.
    """
    message_lower = message.lower()

    timezone_map = {
        r"\bhong\s+kong\b": "Asia/Hong_Kong",
        r"\bhk\b": "Asia/Hong_Kong",
        r"\bnew\s+york\b": "America/New_York",
        r"\bnyc\b": "America/New_York",
        r"\bn\.?y\.?\b": "America/New_York",
        r"\blondon\b": "Europe/London",
        r"\bparis\b": "Europe/Paris",
        r"\btokyo\b": "Asia/Tokyo",
        r"\bsydney\b": "Australia/Sydney",
        r"\blos\s+angeles\b": "America/Los_Angeles",
        r"\bla\b": "America/Los_Angeles",
        r"\bsf\b": "America/Los_Angeles",
        r"\bsan\s+francisco\b": "America/Los_Angeles",
        r"\bsingapore\b": "Asia/Singapore",
        r"\bbeijing\b": "Asia/Shanghai",
        r"\bshanghai\b": "Asia/Shanghai",
        r"\bberlin\b": "Europe/Berlin",
        r"\brome\b": "Europe/Rome",
        r"\bmadrid\b": "Europe/Madrid",
        r"\bamsterdam\b": "Europe/Amsterdam",
        r"\bstockholm\b": "Europe/Stockholm",
        r"\bchicago\b": "America/Chicago",
        r"\bhouston\b": "America/Chicago",
        r"\bphoenix\b": "America/Phoenix",
        r"\bdenver\b": "America/Denver",
        r"\bseattle\b": "America/Los_Angeles",
        r"\bboston\b": "America/New_York",
        r"\bmumbai\b": "Asia/Kolkata",
        r"\bnew\s+delhi\b": "Asia/Kolkata",
        r"\bdelhi\b": "Asia/Kolkata",
        r"\bkolkata\b": "Asia/Kolkata",
        r"\bseoul\b": "Asia/Seoul",
        r"\bbangkok\b": "Asia/Bangkok",
        r"\bjakarta\b": "Asia/Jakarta",
        r"\bmelbourne\b": "Australia/Melbourne",
        r"\bauckland\b": "Pacific/Auckland",
        r"\bcairo\b": "Africa/Cairo",
        r"\bnairobi\b": "Africa/Nairobi",
        r"\blagos\b": "Africa/Lagos",
        r"\bsao\s+paulo\b": "America/Sao_Paulo",
        r"\bbuenos\s+aires\b": "America/Argentina/Buenos_Aires",
        r"\bmexico\s+city\b": "America/Mexico_City",
        r"\btoront[oa]\b": "America/Toronto",
        r"\bvancouver\b": "America/Vancouver",
        r"\bdubal\b": "Asia/Dubai",  # common misspelling kept for robustness
        r"\bdubai\b": "Asia/Dubai",
        r"\bmuscat\b": "Asia/Muscat",
        r"\briyadh\b": "Asia/Riyadh",
        r"\bmosc[ao]w\b": "Europe/Moscow",
    }

    for pattern, tz in timezone_map.items():
        if re.search(pattern, message_lower):
            return tz

    return None


def extract_city_from_message(message: str) -> Optional[str]:
    """Extract a city name from a message for weather / time lookups.

    Parameters
    ----------
    message : str
        User message text.

    Returns
    -------
    str | None
        Title-cased city name, or ``None`` if no recognisable city was found.
    """
    message_lower = message.lower()

    # Ordered list of (regex, canonical city name) pairs.
    # Multi-word names must come before single-word names to avoid partial matches.
    city_map = [
        (r"\bhong\s+kong\b", "Hong Kong"),
        (r"\bnew\s+york\s+city\b", "New York"),
        (r"\bnew\s+york\b", "New York"),
        (r"\bnyc\b", "New York"),
        (r"\blos\s+angeles\b", "Los Angeles"),
        (r"\bsan\s+francisco\b", "San Francisco"),
        (r"\bnew\s+delhi\b", "New Delhi"),
        (r"\bsao\s+paulo\b", "Sao Paulo"),
        (r"\bbuenos\s+aires\b", "Buenos Aires"),
        (r"\bmexico\s+city\b", "Mexico City"),
        (r"\bkuala\s+lumpur\b", "Kuala Lumpur"),
        (r"\brio\s+de\s+janeiro\b", "Rio de Janeiro"),
        (r"\blondon\b", "London"),
        (r"\bparis\b", "Paris"),
        (r"\btokyo\b", "Tokyo"),
        (r"\bsydney\b", "Sydney"),
        (r"\bsingapore\b", "Singapore"),
        (r"\bbeijing\b", "Beijing"),
        (r"\bshanghai\b", "Shanghai"),
        (r"\bberlin\b", "Berlin"),
        (r"\brome\b", "Rome"),
        (r"\bmadrid\b", "Madrid"),
        (r"\bamsterdam\b", "Amsterdam"),
        (r"\bstockholm\b", "Stockholm"),
        (r"\bchicago\b", "Chicago"),
        (r"\bhouston\b", "Houston"),
        (r"\bphoenix\b", "Phoenix"),
        (r"\bdenver\b", "Denver"),
        (r"\bseattle\b", "Seattle"),
        (r"\bboston\b", "Boston"),
        (r"\bmumbai\b", "Mumbai"),
        (r"\bdelhi\b", "Delhi"),
        (r"\bkolkata\b", "Kolkata"),
        (r"\bseoul\b", "Seoul"),
        (r"\bbangkok\b", "Bangkok"),
        (r"\bjakarta\b", "Jakarta"),
        (r"\bmelbourne\b", "Melbourne"),
        (r"\bauckland\b", "Auckland"),
        (r"\bcairo\b", "Cairo"),
        (r"\bnairobi\b", "Nairobi"),
        (r"\blagos\b", "Lagos"),
        (r"\btoront[oa]\b", "Toronto"),
        (r"\bvancouver\b", "Vancouver"),
        (r"\bdubai\b", "Dubai"),
        (r"\bmuscat\b", "Muscat"),
        (r"\briyadh\b", "Riyadh"),
        (r"\bmoscow\b", "Moscow"),
    ]

    for pattern, city in city_map:
        if re.search(pattern, message_lower):
            return city

    return None
