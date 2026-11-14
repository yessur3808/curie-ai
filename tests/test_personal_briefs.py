from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.personal_briefs import PersonalBriefWorker, weather_brief, analytics_brief


def forecast():
    return {
        "city": "Hong Kong",
        "days": [
            {
                "date": f"2026-11-{15+i}",
                "low": 20,
                "high": 25 + i,
                "description": "Cloudy",
                "precipitation_probability": 60 if i == 0 else 10,
            }
            for i in range(4)
        ],
    }


def test_weather_compares_four_days_and_gives_grounded_advice():
    text = weather_brief(forecast())
    assert "Today: 20–25°C" in text
    assert "3°C warmer" in text and "umbrella" in text
    assert "Open-Meteo" in text


@pytest.mark.asyncio
async def test_requested_weather_window_overrides_quiet_hours_once():
    profile = {"morning_weather_enabled": True, "timezone": "Asia/Hong_Kong"}
    worker = PersonalBriefWorker(AsyncMock())
    with (
        patch(
            "services.personal_briefs.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch(
            "services.personal_briefs.UserManager.update_user_profile",
            side_effect=lambda _, changes: profile.update(changes),
        ),
        patch(
            "utils.weather.get_weather_range", new=AsyncMock(return_value=forecast())
        ) as fetch,
        patch.object(worker, "_deliver", new=AsyncMock(return_value=True)) as send,
    ):
        morning = datetime(2026, 11, 14, 22, 30, tzinfo=timezone.utc)
        await worker.run_owner("owner", morning - timedelta(minutes=1))
        send.assert_not_called()
        await worker.run_owner("owner", morning)
        await worker.run_owner("owner", morning + timedelta(minutes=1))
        send.assert_awaited_once()
        fetch.assert_awaited_once()


def test_analytics_ignores_noise_but_reports_change_and_stale_data():
    now = datetime.now(timezone.utc)
    report = {
        "project": {"name": "App"},
        "status": "live",
        "days": 7,
        "updatedAt": now.isoformat(),
        "totals": {"activeUsers": 102, "screenPageViews": 200},
        "previous": {"activeUsers": 100},
    }
    assert analytics_brief([report], now) is None
    report["totals"]["activeUsers"] = 150
    text, signature = analytics_brief([report], now)
    assert "up 50%" in text
    report["updatedAt"] = (now - timedelta(days=3)).isoformat()
    text, other = analytics_brief([report], now)
    assert "stale data" in text and "150" not in text
    assert signature != other
