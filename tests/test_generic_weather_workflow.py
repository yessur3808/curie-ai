"""Generic dated weather uses the forecast-capable provider, not a range assumption."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition
from agent.tooling.weather_tool import WeatherTool


@pytest.mark.parametrize("day,offset", [("today", 0), ("tomorrow", 1)])
@pytest.mark.parametrize(
    "wording",
    [
        "Please check the weather in Wellington {day}.",
        "What is the weather in Kyoto {day}?",
    ],
)
def test_generic_weather_passes_exact_day_offset(monkeypatch, day, offset, wording):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": "UTC"},
    )
    calls, providers = [], []
    city = "Wellington" if "Wellington" in wording else "Kyoto"

    async def provider(name, unit="metric", day_offset=0):
        providers.append((name, unit, day_offset))
        return {
            "city": name,
            "description": "Clear",
            "temperature": 21,
            "tips": [],
            "source": "sandbox",
            "date": "synthetic",
        }

    async def blocked_range(*a, **kw):
        raise AssertionError("Generic weather uses the day-offset provider")

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        assert name == "weather"
        return await WeatherTool().execute(params, context)

    monkeypatch.setattr("utils.weather.get_weather", provider)
    monkeypatch.setattr("utils.weather.get_weather_range", blocked_range)
    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    prompt = wording.format(day=day)
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "generic-weather",
                "external_user_id": "generic-weather",
                "external_chat_id": "generic-weather",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == [("weather", {"query": prompt})]
    assert providers == [(city, "metric", offset)]
    assert result["execution_status"] == "completed"


def test_generic_tomorrow_provider_selects_second_forecast_day(monkeypatch):
    import httpx
    from unittest.mock import AsyncMock
    from utils import weather

    monkeypatch.setattr(
        weather,
        "_geocode",
        AsyncMock(
            return_value={
                "name": "Kyoto",
                "latitude": 35,
                "longitude": 135,
                "country": "Japan",
            }
        ),
    )
    response = httpx.Response(
        200,
        request=httpx.Request("GET", weather._FORECAST_URL),
        json={
            "daily": {
                "time": ["2027-01-01", "2027-01-02"],
                "weather_code": [0, 61],
                "rain_sum": [0, 4],
                "temperature_2m_min": [10, 14],
                "temperature_2m_max": [20, 24],
                "precipitation_probability_max": [0, 80],
            }
        },
    )
    fetch = AsyncMock(return_value=response)
    monkeypatch.setattr(weather, "_get", fetch)
    result = asyncio.run(weather.get_weather("Kyoto", day_offset=1))
    assert fetch.await_count == 1
    params = fetch.await_args.kwargs["params"]
    assert params["forecast_days"] == 2
    assert "daily" in params and "current" not in params
    assert result["date"] == "2027-01-02"
    assert (result["low"], result["high"], result["temperature"]) == (14, 24, 19)
    assert result["precipitation_probability"] == 80
