from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agent.tooling.contracts import ToolContext
from agent.tooling.weather_tool import WeatherTool, _requested_range
from memory.local_store import save_personal_item
from services.current_awareness import (
    CurrentAwarenessService,
    awareness_context,
    current_headlines,
    KIND,
)
from services.proactive_messaging import (
    ProactiveMessagingService,
    _companion_draft_prompt,
)
from utils.temporal_context import local_now, recent_rows, temporal_guidance

NOW = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
PROFILE = {
    "timezone": "Asia/Hong_Kong",
    "location": "Hong Kong",
    "hourly_awareness_enabled": True,
}


def headline(hours=0, domain="reuters.com"):
    return {
        "headline": "A reported development",
        "url": f"https://{domain}/story/{hours}",
        "domain": domain,
        "publisher": domain,
        "published_at": (NOW - timedelta(hours=hours)).isoformat(),
    }


def test_past_dates_are_not_silently_moved_into_next_year():
    assert _requested_range("September 30 to October 9", NOW.date()) == (
        date(2026, 9, 30),
        date(2026, 10, 9),
    )
    assert _requested_range("September 30 to October 9 next year", NOW.date())[
        1
    ] == date(2027, 10, 9)
    assert _requested_range("September 30 to October 9 2028", NOW.date())[1] == date(
        2028, 10, 9
    )
    assert _requested_range("30 September to 9th of October", NOW.date()) == (
        date(2026, 9, 30),
        date(2026, 10, 9),
    )


def test_current_time_is_local_and_historical_rows_are_not_proactive_topics():
    assert local_now(PROFILE, NOW).hour == 10
    assert "2026-10-25" in temporal_guidance(PROFILE, NOW)
    assert local_now(PROFILE, NOW.replace(hour=20)).date() == date(2026, 10, 26)
    rows = [
        {"content": "old trip", "created_at": "2026-09-25T02:00:00+00:00"},
        {"content": "new topic", "created_at": NOW.isoformat()},
        {"content": "unknown age"},
    ]
    assert [row["content"] for row in recent_rows(rows, now=NOW)] == ["new topic"]


def test_proactive_draft_has_a_full_date_and_rejects_expired_trip_offer():
    prompt = _companion_draft_prompt(
        persona={},
        timezone_name="Asia/Hong_Kong",
        now=NOW,
        user_history=["trip September 30 to October 9"],
        recent_assistant=[],
    )
    assert '"current_date": "2026-10-25"' in prompt
    with patch("utils.temporal_context.local_now", return_value=NOW):
        assert not ProactiveMessagingService._prediction_is_grounded(
            {
                "suggestion": "Check the forecast from September 30 to October 9?",
                "reason": "trip",
                "evidence_count": 2,
            },
            PROFILE,
            [("user", "trip"), ("user", "trip")],
        )


def test_proactive_drafts_do_not_cross_midnight():
    profile = {
        **PROFILE,
        "proactive_companion_drafts": [
            {
                "message": "How’s your day going?",
                "created_at": (NOW - timedelta(days=1)).isoformat(),
                "expires_at": (NOW + timedelta(days=1)).isoformat(),
            }
        ],
    }
    assert ProactiveMessagingService._clean_companion_drafts(profile, [], [], NOW) == []


def test_headlines_require_recent_publication_and_reputable_publisher():
    data = current_headlines(
        [headline(), headline(25), headline(-2), headline(1, "reddit.com")], NOW
    )
    assert len(data) == 1
    assert data[0]["evidence_level"].startswith("publisher headline only")


@pytest.mark.asyncio
async def test_hourly_refresh_is_cached_owner_scoped_and_not_a_message():
    weather, news = AsyncMock(
        return_value={
            "temperature": 28,
            "source": "Open-Meteo",
            "observed_at": NOW.isoformat(),
        }
    ), AsyncMock(return_value=[headline()])
    service = CurrentAwarenessService(weather, news)
    first = await service.refresh("owner", PROFILE, now=NOW)
    second = await service.refresh("owner", PROFILE, now=NOW + timedelta(minutes=59))
    assert first == second
    assert weather.await_count == 1
    assert news.await_count == 2
    assert (
        awareness_context("other", PROFILE, now=NOW)["status"] == "unavailable_or_stale"
    )
    assert awareness_context("owner", PROFILE, now=NOW)["weather"]["temperature"] == 28
    assert (
        awareness_context("owner", PROFILE, now=NOW + timedelta(hours=1))["status"]
        == "unavailable_or_stale"
    )
    await service.refresh("owner", PROFILE, now=NOW + timedelta(hours=1))
    assert weather.await_count == 2


@pytest.mark.asyncio
async def test_failure_does_not_make_old_weather_fresh():
    service = CurrentAwarenessService(
        AsyncMock(return_value={"temperature": 28, "observed_at": NOW.isoformat()}),
        AsyncMock(return_value=[headline()]),
    )
    await service.refresh("owner", PROFILE, now=NOW)
    service.weather = AsyncMock(side_effect=TimeoutError)
    service.news = AsyncMock(side_effect=TimeoutError)
    result = await service.refresh("owner", PROFILE, now=NOW + timedelta(hours=1))
    assert result["status"] == "degraded"
    assert result["weather"] is None and result["news"] == []


@pytest.mark.asyncio
async def test_fetching_an_old_weather_observation_does_not_make_it_current():
    service = CurrentAwarenessService(
        AsyncMock(
            return_value={
                "temperature": 28,
                "observed_at": (NOW - timedelta(hours=3)).isoformat(),
            }
        ),
        AsyncMock(return_value=[headline()]),
    )
    result = await service.refresh("owner", PROFILE, now=NOW)
    assert result["weather"] is None
    assert "stale_or_undated_weather" in result["errors"]


@pytest.mark.asyncio
async def test_refresh_at_local_midnight_does_not_reuse_yesterdays_cache():
    before = NOW.replace(hour=15, minute=50)
    after = before + timedelta(minutes=20)
    weather = AsyncMock(return_value={"observed_at": before.isoformat()})
    service = CurrentAwarenessService(weather, AsyncMock(return_value=[headline()]))
    await service.refresh("owner", PROFILE, now=before)
    result = await service.refresh("owner", PROFILE, now=after)
    assert weather.await_count == 2
    assert result["local_date"] == "2026-10-26"


def test_midnight_and_location_change_invalidate_snapshot():
    save_personal_item(
        "owner",
        KIND,
        {
            "id": "snapshot",
            "location": "Hong Kong",
            "local_date": "2026-10-24",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        },
    )
    assert (
        awareness_context("owner", PROFILE, now=NOW)["status"] == "unavailable_or_stale"
    )


@pytest.mark.asyncio
async def test_today_weather_uses_profile_not_old_trip():
    from memory.session_store import get_session_manager

    sm = get_session_manager()
    sm.add_message(
        "telegram", "owner", "user", "Los Angeles from September 30 to October 9"
    )
    fetch = AsyncMock(
        return_value={"city": "Hong Kong", "description": "Clear", "temperature": 28}
    )
    with patch("utils.weather.get_weather", fetch):
        response = await WeatherTool().execute(
            {"query": "What is today's weather?"},
            ToolContext("owner", platform="telegram", profile=PROFILE),
        )
    assert "Hong Kong" in response.text
    fetch.assert_awaited_once_with("Hong Kong", day_offset=0)


@pytest.mark.asyncio
async def test_historical_forecast_rejected_without_network_call():
    with (
        patch("utils.temporal_context.local_now", return_value=NOW),
        patch("utils.weather.get_weather_range", AsyncMock()) as fetch,
    ):
        response = await WeatherTool().execute(
            {"query": "Weather in Los Angeles from September 30 to October 9"},
            ToolContext("owner", profile=PROFILE),
        )
    assert "dates have passed" in response.text
    fetch.assert_not_called()
