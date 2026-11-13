"""Owner-local calendar dates, approval drafts, and DST boundaries."""

import asyncio
from datetime import datetime, timezone

import pytest

from agent.intent_router import _calendar_window, classify_request
from agent.routing import route_operational_request, route_request


@pytest.fixture
def clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 31, 20, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("agent.intent_router.datetime", FixedDateTime)
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")


@pytest.mark.parametrize("route", [route_operational_request, route_request])
def test_owner_local_tomorrow_before_routing_and_approval(clock, monkeypatch, route):
    monkeypatch.setattr(
        "memory.UserManager.get_user_profile",
        lambda owner: {
            "timezone": "Asia/Hong_Kong" if owner == "hk" else "America/New_York"
        },
    )

    def run(text, owner):
        result = route(text, owner)
        return asyncio.run(result) if asyncio.iscoroutine(result) else result

    hk = run("Could you check what I've got on tomorrow?", "hk")
    ny = run("Have a look at my calendar for tomorrow.", "ny")
    assert hk.parameters["time_min"] == "2026-11-02T00:00:00+08:00"
    assert ny.parameters["time_min"] == "2026-11-01T00:00:00-04:00"
    assert ny.parameters["time_max"] == "2026-11-02T00:00:00-05:00"
    draft = run(
        "Schedule a dentist appointment tomorrow at 9:30 am on my calendar", "hk"
    )
    assert draft.parameters["start"] == "2026-11-02T09:30:00+08:00"
    assert draft.parameters["timezone"] == "Asia/Hong_Kong"
    assert draft.approval_required


@pytest.mark.parametrize(
    "day,hour",
    [
        ("2026-03-08", "2:30 am"),
        ("2026-11-01", "1:30 am"),
        ("2026-02-30", "9 am"),
    ],
)
def test_invalid_or_ambiguous_local_time_needs_clarification(day, hour):
    result = classify_request(
        f"Schedule a meeting on {day} at {hour}", timezone_name="America/New_York"
    )
    assert result.action == "clarify"


def test_duration_is_elapsed_time_across_dst():
    result = classify_request(
        "Schedule a meeting on 2026-03-08 at 1:30 am for 2 hours",
        timezone_name="America/New_York",
    )
    assert result.params["start"] == "2026-03-08T01:30:00-05:00"
    assert result.params["end"] == "2026-03-08T04:30:00-04:00"


def test_bad_profile_zone_falls_back_to_configured_zone(clock, monkeypatch):
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Asia/Hong_Kong")
    assert (
        _calendar_window("tomorrow", "invalid/zone")[0] == "2026-11-02T00:00:00+08:00"
    )


def test_explicit_calendar_timestamps_not_reinterpreted(clock):
    result = classify_request(
        "/calendar create Dentist | 2026-11-02T09:00:00+00:00 | 2026-11-02T10:00:00+00:00 | UTC | Central | Check-up",
        timezone_name="Asia/Hong_Kong",
    )
    assert result.params["start"] == "2026-11-02T09:00:00+00:00"
    assert result.params["timezone"] == "UTC"
