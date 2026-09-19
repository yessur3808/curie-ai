"""Keyless live weather and forecast data backed by Open-Meteo."""

from __future__ import annotations

from datetime import date
import aiohttp
import httpx
from utils.ttl_cache import TTLCache

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_WMO = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Light freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    85: "Light snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Severe thunderstorm with hail",
}
_WET_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99})
_GEOCODE_CACHE = TTLCache(
    ttl_seconds=3600,
    max_size=256,
    name="public_geocoding",
    owner_scope="public",
    invalidation_event="TTL expiry or geocoder configuration change",
    sensitivity="public",
)


def reset_cache() -> None:
    """Clear process-local geocoding results for tests and runtime reloads."""
    _GEOCODE_CACHE.clear()


async def _geocode(client: httpx.AsyncClient, city: str) -> dict:
    key = city.strip().casefold()
    cached = _GEOCODE_CACHE.get(key)
    if cached is not None:
        return cached
    geo = await client.get(
        _GEOCODE_URL,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
    )
    geo.raise_for_status()
    matches = geo.json().get("results") or []
    if not matches:
        raise ValueError(f"Could not find a location named {city!r}")
    place = matches[0]
    _GEOCODE_CACHE.set(key, place)
    return place


async def get_weather(city: str, unit: str = "metric", day_offset: int = 0):
    """Return current conditions or a daily forecast with hard network timeouts."""
    temp_unit = "fahrenheit" if unit == "imperial" else "celsius"
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        place = await _geocode(client, city)
        common = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "temperature_unit": temp_unit,
            "timezone": "auto",
        }
        if day_offset:
            response = await client.get(
                _FORECAST_URL,
                params={
                    **common,
                    "forecast_days": max(2, day_offset + 1),
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,rain_sum",
                },
            )
            response.raise_for_status()
            daily = response.json()["daily"]
            if day_offset >= len(daily["time"]):
                raise ValueError("Forecast is not available that far ahead")
            code = int(daily["weather_code"][day_offset])
            rain = float(daily["rain_sum"][day_offset] or 0)
            low, high = (
                daily["temperature_2m_min"][day_offset],
                daily["temperature_2m_max"][day_offset],
            )
            temperature = round((float(low) + float(high)) / 2)
            result = {
                "date": daily["time"][day_offset],
                "low": low,
                "high": high,
                "precipitation_probability": daily["precipitation_probability_max"][
                    day_offset
                ],
            }
        else:
            response = await client.get(
                _FORECAST_URL,
                params={
                    **common,
                    "current": (
                        "temperature_2m,apparent_temperature,relative_humidity_2m,"
                        "precipitation,rain,showers,weather_code"
                    ),
                },
            )
            response.raise_for_status()
            current = response.json()["current"]
            code = int(current["weather_code"])
            rain = float(current.get("rain", 0) or 0) + float(
                current.get("showers", 0) or 0
            )
            temperature = current["temperature_2m"]
            result = {
                "observed_at": current.get("time"),
                "apparent_temperature": current.get("apparent_temperature"),
                "relative_humidity": current.get("relative_humidity_2m"),
            }
    is_raining = rain > 0 or code in _WET_CODES
    tips = []
    if temperature < 16:
        tips.append("Take a jacket.")
    elif temperature > 30:
        tips.append("Stay hydrated.")
    if is_raining:
        tips.append("Bring an umbrella.")
    result.update(
        {
            "city": place.get("name", city),
            "country": place.get("country"),
            "temperature": temperature,
            "description": _WMO.get(code, "Unknown conditions"),
            "weather_code": code,
            "rain_mm": rain,
            "is_raining": is_raining,
            "tips": tips,
            "source": "Open-Meteo",
        }
    )
    return result


def extract_city_from_message(message):
    """Compatibility wrapper around the canonical city recognizer."""
    from utils.datetime_info import extract_city_from_message as extract_city

    return extract_city(message)


async def get_weather_range(
    city: str, start_date: date, end_date: date, unit: str = "metric"
) -> dict:
    """Return one bounded daily forecast range in a single provider request."""
    if end_date < start_date:
        raise ValueError("Forecast end date must not be before its start date")
    today = date.today()
    start_offset = (start_date - today).days
    end_offset = (end_date - today).days
    if start_offset < 0:
        raise ValueError(
            "Historical weather is not available through this forecast tool"
        )
    if end_offset > 15:
        raise ValueError("Forecast is not available that far ahead")

    temp_unit = "fahrenheit" if unit == "imperial" else "celsius"
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        place = await _geocode(client, city)
        response = await client.get(
            _FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "temperature_unit": temp_unit,
                "timezone": "auto",
                "forecast_days": end_offset + 1,
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,rain_sum"
                ),
            },
        )
        response.raise_for_status()
        daily = response.json()["daily"]

    days = []
    for index in range(start_offset, end_offset + 1):
        code = int(daily["weather_code"][index])
        rain = float(daily["rain_sum"][index] or 0)
        days.append(
            {
                "date": daily["time"][index],
                "low": daily["temperature_2m_min"][index],
                "high": daily["temperature_2m_max"][index],
                "precipitation_probability": daily["precipitation_probability_max"][
                    index
                ],
                "description": _WMO.get(code, "Unknown conditions"),
                "rain_mm": rain,
                "is_raining": rain > 0 or code in _WET_CODES,
            }
        )
    return {
        "city": place.get("name", city),
        "country": place.get("country"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": days,
        "source": "Open-Meteo",
    }


async def get_hko_typhoon_signal():
    url = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=warnsum&lang=en"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
            signals = []
            if data.get("tcSignal"):
                signals.append(f"Typhoon Signal: {data['tcSignal']}")
            if data.get("WFIRE"):
                signals.append(f"Fire Danger Warning: {data['WFIRE']}")
            return " | ".join(signals) if signals else None
