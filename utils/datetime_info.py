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
    except Exception as e:
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
    
    Args:
        message: User message text
    
    Returns:
        Timezone string (e.g., 'Asia/Hong_Kong', 'America/New_York') if a location
        is found in the message, otherwise 'UTC' as the default fallback.
    """
    message_lower = message.lower()
    
    # Map common location mentions to timezones
    timezone_map = {
        "hong kong": "Asia/Hong_Kong",
        "hk": "Asia/Hong_Kong",
        "new york": "America/New_York",
        "ny": "America/New_York",
        "london": "Europe/London",
        "paris": "Europe/Paris",
        "tokyo": "Asia/Tokyo",
        "sydney": "Australia/Sydney",
        "los angeles": "America/Los_Angeles",
        "la": "America/Los_Angeles",
        "singapore": "Asia/Singapore",
        "beijing": "Asia/Shanghai",
        "shanghai": "Asia/Shanghai",
    }
    
    for location, tz in timezone_map.items():
        if location in message_lower:
            return tz
    
    return "UTC"
