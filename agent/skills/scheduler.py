# agent/skills/scheduler.py
"""
Reminders & Scheduling skill.

Allows users to set, list, and delete personal reminders that are
persisted in MongoDB.  The proactive messaging service polls this
store and delivers due reminders.

Natural-language examples handled
----------------------------------
  "remind me to call mom at 3pm"
  "set a reminder for 2026-04-01 at 09:00 – team standup"
  "remind me in 30 minutes to take my medication"
  "list my reminders"
  "delete reminder 3"
  "cancel all reminders"

MongoDB collection: ``reminders``
Document schema
---------------
{
    "_id": ObjectId,
    "internal_id": str,          # owner's internal user ID
    "platform": str,             # originating platform
    "message": str,              # reminder text
    "due_at": datetime (UTC),    # when to fire
    "created_at": datetime (UTC),
    "fired": bool,               # True once delivered
    "snooze_count": int,         # how many times snoozed
}
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy DB access (avoids import-time connection attempts in tests)
# ---------------------------------------------------------------------------

def _get_reminders_collection():
    """Return the MongoDB reminders collection."""
    from memory.database import mongo_db  # noqa: PLC0415
    return mongo_db.reminders


# ---------------------------------------------------------------------------
# Keyword / intent detection
# ---------------------------------------------------------------------------

_REMINDER_KEYWORDS = re.compile(
    r"\b(remind|reminder|reminders|alert|notify|don[\'']?t forget|schedule|set an alarm"
    r"|set a reminder|add a reminder|create a reminder|new reminder)\b",
    re.IGNORECASE,
)

_LIST_KEYWORDS = re.compile(
    r"\b(list|show|what are|get|view|display|all)\b.{0,30}\b(reminder|reminders|alarm|alarms)\b",
    re.IGNORECASE,
)

_DELETE_KEYWORDS = re.compile(
    r"\b(delete|remove|cancel|clear)\b.{0,30}\b(reminder|reminders|alarm|alarms)\b",
    re.IGNORECASE,
)

_TIME_PATTERNS = [
    # "in 30 minutes", "in 2 hours", "in 1 day"
    re.compile(
        r"\bin\s+(?P<amount>\d+)\s+(?P<unit>minute|minutes|min|hour|hours|hr|day|days|week|weeks)\b",
        re.IGNORECASE,
    ),
    # "at 3pm", "at 14:30", "at 9:00 am"
    re.compile(
        r"\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?\b",
        re.IGNORECASE,
    ),
    # "tomorrow at …"
    re.compile(r"\btomorrow\b", re.IGNORECASE),
    # ISO-style "2026-04-01" or "04/01/2026"
    re.compile(
        r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b"
        r"|\b(?P<month2>\d{1,2})/(?P<day2>\d{1,2})/(?P<year2>\d{4})\b",
        re.IGNORECASE,
    ),
]


def is_reminder_query(text: str) -> bool:
    """Return True if the text looks like a reminder/scheduling intent."""
    return bool(_REMINDER_KEYWORDS.search(text))


def _parse_due_time(text: str) -> Optional[datetime]:
    """
    Attempt to parse a due datetime from natural language.
    Returns a timezone-aware UTC datetime, or None if unparseable.
    """
    now = datetime.now(timezone.utc)

    # "in N units"
    m = _TIME_PATTERNS[0].search(text)
    if m:
        amount = int(m.group("amount"))
        unit = m.group("unit").lower()
        if unit in ("minute", "minutes", "min"):
            return now + timedelta(minutes=amount)
        if unit in ("hour", "hours", "hr"):
            return now + timedelta(hours=amount)
        if unit in ("day", "days"):
            return now + timedelta(days=amount)
        if unit in ("week", "weeks"):
            return now + timedelta(weeks=amount)

    # "at HH[:MM] [am/pm]"
    m = _TIME_PATTERNS[1].search(text)
    if m:
        hour = int(m.group("hour"))
        minute = int(m.group("minute") or 0)
        ampm = (m.group("ampm") or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if _TIME_PATTERNS[2].search(text):
            # "tomorrow at HH:MM" — always add exactly one day from today
            due += timedelta(days=1)
        elif due <= now:
            # Time already passed today → schedule for tomorrow
            due += timedelta(days=1)
        return due

    # "tomorrow" (without explicit time → next day, same hour)
    if _TIME_PATTERNS[2].search(text):
        return now + timedelta(days=1)

    # ISO date "YYYY-MM-DD"
    m = _TIME_PATTERNS[3].search(text)
    if m:
        try:
            if m.group("year"):
                year, month, day = int(m.group("year")), int(m.group("month")), int(m.group("day"))
            else:
                month, day, year = int(m.group("month2")), int(m.group("day2")), int(m.group("year2"))
            return datetime(year, month, day, 9, 0, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    return None


def _extract_reminder_message(text: str) -> str:
    """Extract the descriptive part of a reminder request."""
    # Strip leading trigger phrase
    cleaned = re.sub(
        r"^\s*(remind me (to|about|that)?|set a reminder (for|to|that)?|"
        r"reminder:?|alert me (to|about)?|don[\'']?t forget to)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Strip trailing time clause ("at 3pm", "in 2 hours", "tomorrow")
    for pat in _TIME_PATTERNS:
        cleaned = pat.sub("", cleaned).strip()

    # Remove filler connectors left at the end
    cleaned = re.sub(r"\s*(–|-|at|in|on|by|before|after)\s*$", "", cleaned).strip()

    return cleaned or text.strip()


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------

def add_reminder(
    internal_id: str,
    platform: str,
    message: str,
    due_at: datetime,
) -> str:
    """
    Persist a new reminder and return a human-readable confirmation.
    """
    col = _get_reminders_collection()
    doc = {
        "internal_id": internal_id,
        "platform": platform,
        "message": message,
        "due_at": due_at,
        "created_at": datetime.now(timezone.utc),
        "fired": False,
        "snooze_count": 0,
    }
    result = col.insert_one(doc)
    logger.info("Reminder created id=%s for user=%s due=%s", result.inserted_id, internal_id, due_at)

    due_str = due_at.strftime("%A, %b %d at %I:%M %p UTC")
    return f"⏰ Got it! I'll remind you to **{message}** on {due_str}."


def list_reminders(internal_id: str) -> str:
    """Return a formatted list of pending reminders for the user."""
    col = _get_reminders_collection()
    now = datetime.now(timezone.utc)
    docs = list(
        col.find(
            {"internal_id": internal_id, "fired": False, "due_at": {"$gte": now}},
            sort=[("due_at", 1)],
        )
    )

    if not docs:
        return "You have no upcoming reminders. 📅"

    lines = ["📋 **Your upcoming reminders:**\n"]
    for idx, doc in enumerate(docs, start=1):
        due_str = doc["due_at"].strftime("%a, %b %d at %I:%M %p UTC")
        lines.append(f"{idx}. {doc['message']} — _{due_str}_")
    return "\n".join(lines)


def delete_reminder(internal_id: str, index: Optional[int] = None) -> str:
    """
    Delete reminder(s).
    - If ``index`` is given, delete the Nth upcoming reminder.
    - If ``index`` is None, delete **all** pending reminders.
    """
    col = _get_reminders_collection()
    now = datetime.now(timezone.utc)

    if index is None:
        result = col.delete_many({"internal_id": internal_id, "fired": False})
        count = result.deleted_count
        return f"🗑️ Deleted all {count} reminder(s)." if count else "No reminders to delete."

    docs = list(
        col.find(
            {"internal_id": internal_id, "fired": False, "due_at": {"$gte": now}},
            sort=[("due_at", 1)],
        )
    )
    if not docs or index < 1 or index > len(docs):
        return f"Couldn't find reminder #{index}. Use 'list my reminders' to see your current list."

    target = docs[index - 1]
    col.delete_one({"_id": target["_id"]})
    return f"🗑️ Deleted reminder: _{target['message']}_"


def get_due_reminders(internal_id: str) -> list[dict]:
    """Return all fired=False reminders whose due_at <= now."""
    col = _get_reminders_collection()
    now = datetime.now(timezone.utc)
    docs = list(
        col.find({"internal_id": internal_id, "fired": False, "due_at": {"$lte": now}})
    )
    return docs


def mark_reminder_fired(reminder_id) -> None:
    """Mark a reminder as delivered."""
    from bson import ObjectId  # noqa: PLC0415
    col = _get_reminders_collection()
    col.update_one({"_id": ObjectId(str(reminder_id))}, {"$set": {"fired": True}})


# ---------------------------------------------------------------------------
# Main skill handler
# ---------------------------------------------------------------------------

async def handle_reminder_query(
    text: str,
    internal_id: str = "unknown",
    platform: str = "unknown",
) -> Optional[str]:
    """
    Parse a reminder-related message and return a response string.
    Returns None if the text is not a reminder intent.
    """
    if not is_reminder_query(text) and not _LIST_KEYWORDS.search(text) and not _DELETE_KEYWORDS.search(text):
        return None

    # Deletion takes priority over listing (e.g. "cancel all reminders" contains "all"
    # which _LIST_KEYWORDS would otherwise match before deletion is checked).
    delete_match = _DELETE_KEYWORDS.search(text)
    if delete_match:
        # "delete reminder 2" → extract index
        num_match = re.search(r"\b(\d+)\b", text[delete_match.end():])
        idx = int(num_match.group(1)) if num_match else None
        try:
            return delete_reminder(internal_id, idx)
        except Exception as exc:
            logger.error("Error deleting reminder: %s", exc)
            return "Sorry, I couldn't delete the reminder right now."

    # Listing
    if _LIST_KEYWORDS.search(text):
        try:
            return list_reminders(internal_id)
        except Exception as exc:
            logger.error("Error listing reminders: %s", exc)
            return "Sorry, I couldn't fetch your reminders right now."

    # Creation
    due_at = _parse_due_time(text)
    if due_at is None:
        # No parseable time → ask user
        return (
            "I'd love to set a reminder! When should I remind you? "
            "You can say something like 'remind me in 30 minutes' or 'remind me tomorrow at 9am'."
        )

    message = _extract_reminder_message(text)
    if not message:
        message = "your reminder"

    try:
        return add_reminder(internal_id, platform, message, due_at)
    except Exception as exc:
        logger.error("Error adding reminder: %s", exc)
        return "Sorry, I couldn't save your reminder right now. Please try again."
