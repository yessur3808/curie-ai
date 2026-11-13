"""Calendar reads must use owner-local midnight, including DST transitions."""

import asyncio
from datetime import datetime, timezone

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition, ToolResult


@pytest.mark.parametrize(
    "day,instant,zone,start,end,hours",
    [
        (
            "this week",
            "2026-09-23T00:00:00+00:00",
            "Pacific/Auckland",
            "2026-09-21T00:00:00+12:00",
            "2026-09-28T00:00:00+13:00",
            167,
        ),
        (
            "this week",
            "2026-04-01T00:00:00+00:00",
            "Pacific/Auckland",
            "2026-03-30T00:00:00+13:00",
            "2026-04-06T00:00:00+12:00",
            169,
        ),
        (
            "today",
            "2026-10-04T02:00:00+00:00",
            "Australia/Adelaide",
            "2026-10-04T00:00:00+09:30",
            "2026-10-05T00:00:00+10:30",
            23,
        ),
        (
            "today",
            "2026-10-04T02:00:00+00:00",
            "Australia/Lord_Howe",
            "2026-10-04T00:00:00+10:30",
            "2026-10-05T00:00:00+11:00",
            23.5,
        ),
        (
            "tomorrow",
            "2026-04-04T02:00:00+00:00",
            "Australia/Lord_Howe",
            "2026-04-05T00:00:00+11:00",
            "2026-04-06T00:00:00+10:30",
            24.5,
        ),
        (
            "today",
            "2026-12-31T18:30:00+00:00",
            "Asia/Kathmandu",
            "2027-01-01T00:00:00+05:45",
            "2027-01-02T00:00:00+05:45",
            24,
        ),
        (
            "this month",
            "2028-02-15T12:00:00+00:00",
            "UTC",
            "2028-02-01T00:00:00+00:00",
            "2028-03-01T00:00:00+00:00",
            696,
        ),
        (
            "next month",
            "2027-01-31T12:00:00+00:00",
            "UTC",
            "2027-02-01T00:00:00+00:00",
            "2027-03-01T00:00:00+00:00",
            672,
        ),
        (
            "next month",
            "2026-12-15T12:00:00+00:00",
            "Asia/Hong_Kong",
            "2027-01-01T00:00:00+08:00",
            "2027-02-01T00:00:00+08:00",
            744,
        ),
        (
            "this month",
            "2026-03-15T12:00:00+00:00",
            "America/New_York",
            "2026-03-01T00:00:00-05:00",
            "2026-04-01T00:00:00-04:00",
            743,
        ),
        (
            "next 30 days",
            "2027-01-31T12:00:00+00:00",
            "UTC",
            "2027-01-31T12:00:00+00:00",
            "2027-03-02T12:00:00+00:00",
            720,
        ),
        (
            "this week",
            "2026-10-28T12:00:00+00:00",
            "America/New_York",
            "2026-10-26T00:00:00-04:00",
            "2026-11-02T00:00:00-05:00",
            169,
        ),
        (
            "next week",
            "2026-10-28T12:00:00+00:00",
            "America/New_York",
            "2026-11-02T00:00:00-05:00",
            "2026-11-09T00:00:00-05:00",
            168,
        ),
        (
            "next 7 days",
            "2026-10-28T12:00:00+00:00",
            "America/New_York",
            "2026-10-28T08:00:00-04:00",
            "2026-11-04T08:00:00-05:00",
            169,
        ),
        (
            "today",
            "2026-03-08T12:00:00+00:00",
            "America/New_York",
            "2026-03-08T00:00:00-05:00",
            "2026-03-09T00:00:00-04:00",
            23,
        ),
        (
            "tomorrow",
            "2026-03-07T12:00:00+00:00",
            "America/New_York",
            "2026-03-08T00:00:00-05:00",
            "2026-03-09T00:00:00-04:00",
            23,
        ),
        (
            "today",
            "2026-11-01T12:00:00+00:00",
            "America/New_York",
            "2026-11-01T00:00:00-04:00",
            "2026-11-02T00:00:00-05:00",
            25,
        ),
        (
            "tomorrow",
            "2026-10-31T12:00:00+00:00",
            "America/New_York",
            "2026-11-01T00:00:00-04:00",
            "2026-11-02T00:00:00-05:00",
            25,
        ),
        (
            "today",
            "2026-12-31T20:00:00+00:00",
            "Asia/Hong_Kong",
            "2027-01-01T00:00:00+08:00",
            "2027-01-02T00:00:00+08:00",
            24,
        ),
        (
            "tomorrow",
            "2026-12-31T20:00:00+00:00",
            "Asia/Hong_Kong",
            "2027-01-02T00:00:00+08:00",
            "2027-01-03T00:00:00+08:00",
            24,
        ),
    ],
)
@pytest.mark.parametrize(
    "wording",
    [
        "Could you check my calendar for {day}?",
        "Have I got anything scheduled for {day}?",
        "Please check what's on my calendar for {day}.",
    ],
)
def test_calendar_workflow_exact_local_day(
    monkeypatch, day, instant, zone, start, end, hours, wording
):
    fixed = datetime.fromisoformat(instant)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz)

    monkeypatch.setattr("agent.intent_router.datetime", FixedDateTime)
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "UTC")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": zone},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    calls = []

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        return ToolResult(
            "No events in the requested period. (Sandbox)", {"events": []}, "sandbox"
        )

    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "calendar-boundary",
                "external_user_id": "calendar-boundary",
                "external_chat_id": "calendar-boundary",
                "message_id": "boundary",
                "text": wording.format(day=day),
            }
        )
    )
    assert len(calls) == 1
    name, params = calls[0]
    assert name == "calendar_list"
    assert params["time_min"] == start
    assert params["time_max"] == end
    assert (
        datetime.fromisoformat(end).astimezone(timezone.utc)
        - datetime.fromisoformat(start).astimezone(timezone.utc)
    ).total_seconds() == hours * 3600
    assert result["execution_status"] == "completed"
