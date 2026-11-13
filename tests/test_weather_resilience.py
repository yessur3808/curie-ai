import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.tooling.errors import ProviderAccessError, user_facing_tool_error
from utils import weather


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("private detail"),
        httpx.ReadError("private detail"),
        502,
        503,
        504,
        408,
    ],
)
async def test_transient_failure_recovers_once(failure):
    request = httpx.Request("GET", weather._FORECAST_URL)
    bad = (
        httpx.Response(failure, request=request)
        if isinstance(failure, int)
        else failure
    )
    good = httpx.Response(200, request=request, json={"daily": {}})
    client = AsyncMock()
    client.get.side_effect = [bad, good]
    assert await weather._get(client, weather._FORECAST_URL, params={}) is good
    assert client.get.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429])
async def test_permanent_errors_and_rate_limits_not_retried(status):
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        status, request=httpx.Request("GET", weather._GEOCODE_URL)
    )
    with pytest.raises(ProviderAccessError) as caught:
        await weather._get(client, weather._GEOCODE_URL, params={})
    assert client.get.await_count == 1
    assert caught.value.http_status == status
    assert caught.value.error_code == f"weather_geocoding_http_{status}"


@pytest.mark.asyncio
async def test_exhaustion_has_safe_diagnostic():
    client = AsyncMock()
    client.get.side_effect = httpx.ReadTimeout("SECRET raw request")
    with pytest.raises(ProviderAccessError) as caught:
        await weather._get(client, weather._FORECAST_URL, params={})
    assert client.get.await_count == 2
    assert caught.value.error_code == "weather_forecast_ReadTimeout"
    assert "SECRET" not in str(caught.value)
    assert "two attempts" in user_facing_tool_error(caught.value, "weather")


@pytest.mark.asyncio
async def test_cancellation_is_not_retried():
    client = AsyncMock()
    client.get.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await weather._get(client, weather._FORECAST_URL, params={})
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_total_deadline_stops_slow_provider(monkeypatch):
    monkeypatch.setattr(weather, "_REQUEST_DEADLINE", 0.01)
    stopped = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    client = AsyncMock()
    client.get.side_effect = slow
    with pytest.raises(ProviderAccessError) as caught:
        await weather._get(client, weather._FORECAST_URL, params={})
    assert caught.value.error_code == "weather_forecast_deadline"
    assert stopped.is_set()
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_geocoding_failure_does_not_poison_cache():
    weather.reset_cache()
    client = AsyncMock()
    request = httpx.Request("GET", weather._GEOCODE_URL)
    client.get.return_value = httpx.Response(503, request=request)
    with pytest.raises(ProviderAccessError) as caught:
        await weather._geocode(client, "Hong Kong")
    assert caught.value.http_status == 503
    assert client.get.await_count == 2
    client.get.return_value = httpx.Response(
        200,
        request=request,
        json={"results": [{"name": "Hong Kong", "latitude": 22.3, "longitude": 114.2}]},
    )
    result = await weather._geocode(client, "Hong Kong")
    assert result["name"] == "Hong Kong"
    assert await weather._geocode(client, "Hong Kong") == result
    assert client.get.await_count == 3
    weather.reset_cache()
