# utils/datetime_info.py

from datetime import datetime
import pytz

def get_current_datetime(timezone_str: str = "UTC"):
    """
    Get current date and time for a specific timezone.
    
    Args:
        timezone_str: Timezone string (e.g., 'UTC', 'Asia/Hong_Kong', 'America/New_York')
    
    Returns:
        dict with formatted date, time, timezone info
    """
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "timezone": timezone_str,
            "formatted": now.strftime("%A, %B %d, %Y at %I:%M %p %Z"),
            "iso": now.isoformat()
        }
    except pytz.UnknownTimeZoneError as e:
        # Fallback to UTC if timezone is invalid
        tz = pytz.UTC
        now = datetime.now(tz)
        return {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "timezone": "UTC",
            "formatted": now.strftime("%A, %B %d, %Y at %I:%M %p %Z"),
            "iso": now.isoformat(),
            "error": str(e)
        }

def extract_timezone_from_message(message: str) -> str:
    """
    Extract timezone from message based on location mentions.
    Uses word boundary matching to avoid false positives (e.g., "any" won't match "ny").
    
    Args:
        message: User message text
    
    Returns:
        Timezone string (e.g., 'Asia/Hong_Kong', 'America/New_York') if a location
        is found in the message, otherwise 'UTC' as the default fallback.
    """
    import re
    
    message_lower = message.lower()
    
    # Map common location mentions (regex patterns with word boundaries) to timezones
    # Using word boundaries (\b) to prevent false positives
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
        r"\bsingapore\b": "Asia/Singapore",
        r"\bbeijing\b": "Asia/Shanghai",
        r"\bshanghai\b": "Asia/Shanghai",
    }
    
    for pattern, tz in timezone_map.items():
        if re.search(pattern, message_lower):
            return tz
    
    return "UTC"
