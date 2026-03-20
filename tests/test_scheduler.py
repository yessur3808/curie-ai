#!/usr/bin/env python3
"""
Tests for the Reminders & Scheduling skill.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub heavy DB dependencies before importing the skill
for _mod in ("psycopg2", "psycopg2.extras", "psycopg2.extensions",
             "pymongo", "pymongo.collection", "pymongo.errors"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


from agent.skills.scheduler import (  # noqa: E402
    is_reminder_query,
    _parse_due_time,
    _extract_reminder_message,
)

# ------------------------------------------------------------------
# is_reminder_query
# ------------------------------------------------------------------

class TestIsReminderQuery:
    def test_remind_me(self):
        assert is_reminder_query("remind me to call mom at 3pm")

    def test_set_a_reminder(self):
        assert is_reminder_query("set a reminder for tomorrow")

    def test_reminder_keyword(self):
        assert is_reminder_query("reminder: team standup at 9am")

    def test_alert_me(self):
        assert is_reminder_query("alert me in 30 minutes")

    def test_dont_forget(self):
        assert is_reminder_query("don't forget to buy groceries")

    def test_schedule(self):
        assert is_reminder_query("schedule a call with John")

    def test_unrelated_message(self):
        assert not is_reminder_query("what's the weather like today?")

    def test_empty_string(self):
        assert not is_reminder_query("")

    def test_generic_chat(self):
        assert not is_reminder_query("how are you doing?")


# ------------------------------------------------------------------
# _parse_due_time
# ------------------------------------------------------------------

class TestParseDueTime:
    def _now(self):
        return datetime.now(timezone.utc)

    def test_in_minutes(self):
        due = _parse_due_time("remind me in 30 minutes to take my medicine")
        assert due is not None
        delta = due - self._now()
        assert 28 * 60 <= delta.total_seconds() <= 32 * 60

    def test_in_hours(self):
        due = _parse_due_time("remind me in 2 hours")
        assert due is not None
        delta = due - self._now()
        assert 1.9 * 3600 <= delta.total_seconds() <= 2.1 * 3600

    def test_in_days(self):
        due = _parse_due_time("remind me in 3 days")
        assert due is not None
        delta = due - self._now()
        assert 2.9 * 86400 <= delta.total_seconds() <= 3.1 * 86400

    def test_tomorrow(self):
        due = _parse_due_time("remind me tomorrow")
        assert due is not None
        delta = due - self._now()
        assert 0.9 * 86400 <= delta.total_seconds() <= 1.1 * 86400

    def test_at_time_pm(self):
        due = _parse_due_time("remind me at 3pm to call the doctor")
        assert due is not None
        # Hour should be 15
        assert due.hour == 15

    def test_at_time_am(self):
        due = _parse_due_time("remind me at 9am")
        assert due is not None
        assert due.hour == 9

    def test_iso_date(self):
        due = _parse_due_time("remind me on 2030-12-25 for Christmas")
        assert due is not None
        assert due.year == 2030
        assert due.month == 12
        assert due.day == 25

    def test_no_time_returns_none(self):
        due = _parse_due_time("remind me to buy milk")
        assert due is None

    def test_in_weeks(self):
        due = _parse_due_time("remind me in 2 weeks")
        assert due is not None
        delta = due - self._now()
        assert 13 * 86400 <= delta.total_seconds() <= 15 * 86400


# ------------------------------------------------------------------
# _extract_reminder_message
# ------------------------------------------------------------------

class TestExtractReminderMessage:
    def test_remind_me_to(self):
        msg = _extract_reminder_message("remind me to call mom at 3pm")
        assert "call mom" in msg.lower()

    def test_set_a_reminder_for(self):
        msg = _extract_reminder_message("set a reminder for the team meeting tomorrow")
        assert "team meeting" in msg.lower()

    def test_dont_forget_to(self):
        msg = _extract_reminder_message("don't forget to take your medication in 1 hour")
        assert "take" in msg.lower() or "medication" in msg.lower()

    def test_reminder_colon(self):
        msg = _extract_reminder_message("reminder: call the dentist at 9am")
        assert "call the dentist" in msg.lower()


# ------------------------------------------------------------------
# handle_reminder_query (async, with DB mocked)
# ------------------------------------------------------------------

class TestHandleReminderQueryAsync:
    @pytest.mark.asyncio
    async def test_returns_none_for_non_reminder(self):
        from agent.skills.scheduler import handle_reminder_query
        result = await handle_reminder_query("what's the weather?")
        assert result is None

    @pytest.mark.asyncio
    async def test_asks_for_time_when_none_found(self):
        from agent.skills.scheduler import handle_reminder_query
        result = await handle_reminder_query("remind me to buy milk")
        assert result is not None
        assert "when" in result.lower() or "remind" in result.lower()

    @pytest.mark.asyncio
    async def test_creates_reminder_with_time(self):
        from agent.skills.scheduler import handle_reminder_query

        mock_col = MagicMock()
        mock_col.insert_one.return_value = MagicMock(inserted_id="abc123")

        with patch("agent.skills.scheduler._get_reminders_collection", return_value=mock_col):
            result = await handle_reminder_query(
                "remind me in 30 minutes to call mom",
                internal_id="user_1",
                platform="telegram",
            )

        assert result is not None
        assert "call mom" in result.lower() or "reminder" in result.lower() or "⏰" in result

    @pytest.mark.asyncio
    async def test_lists_reminders_empty(self):
        from agent.skills.scheduler import handle_reminder_query

        mock_col = MagicMock()
        mock_col.find.return_value = []

        with patch("agent.skills.scheduler._get_reminders_collection", return_value=mock_col):
            result = await handle_reminder_query("list my reminders", internal_id="user_1")

        assert result is not None
        assert "no" in result.lower() or "upcoming" in result.lower()

    @pytest.mark.asyncio
    async def test_deletes_all_reminders(self):
        from agent.skills.scheduler import handle_reminder_query

        mock_col = MagicMock()
        mock_col.delete_many.return_value = MagicMock(deleted_count=2)

        with patch("agent.skills.scheduler._get_reminders_collection", return_value=mock_col):
            result = await handle_reminder_query("cancel all reminders", internal_id="user_1")

        assert result is not None
        assert "deleted" in result.lower() or "🗑️" in result


def test_import_guard():
    """Confirm the scheduler module can be imported without DB connections."""
    import agent.skills.scheduler  # noqa: F401
    assert True
