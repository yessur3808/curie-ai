"""Live current-weather and forecast presentation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
import re
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            (),
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        )
    )
    for name in names
}
_DATE_RANGE = re.compile(
    r"\b(?:from\s+)?(?P<m1>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?"
    r"\s*(?:to|through|until|[-–])\s*"
    r"(?:(?P<m2>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\s+)?(?P<d2>\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)


def _requested_range(query: str, today: date | None = None) -> tuple[date, date] | None:
    match = _DATE_RANGE.search(query)
    if not match:
        return None
    today = today or date.today()
    first_month = _MONTHS[match.group("m1").casefold()]
    second_month = _MONTHS[(match.group("m2") or match.group("m1")).casefold()]
    year = today.year
    start = date(year, first_month, int(match.group("d1")))
    end_year = year + (second_month < first_month)
    end = date(end_year, second_month, int(match.group("d2")))
    if end < today:
        start = start.replace(year=start.year + 1)
        end = end.replace(year=end.year + 1)
    return start, end


def _recent_weather_context(
    context: ToolContext,
) -> tuple[str | None, tuple[date, date] | None]:
    if context.platform == "unknown":
        return None, None
    try:
        from memory.session_store import get_session_manager
        from utils.datetime_info import extract_city_from_message

        rows = get_session_manager().get_history(context.platform, context.internal_id)
        city = None
        requested = None
        for row in reversed(rows[-12:]):
            if row.get("role") != "user":
                continue
            content = str(row.get("content", ""))
            city = city or extract_city_from_message(content)
            requested = requested or _requested_range(content)
            if city and requested:
                break
        return city, requested
    except Exception:
        pass
    return None, None


class WeatherTool:
    name = "weather"
    read_only = True

    async def execute(
        self, params: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        from utils.datetime_info import extract_city_from_message
        from utils.weather import get_weather, get_weather_range

        query = str(params.get("query", ""))
        city = extract_city_from_message(query)
        requested = _requested_range(query)
        if not city or (not requested and re.search(r"\bthose dates\b", query, re.I)):
            recent_city, recent_range = _recent_weather_context(context)
            city = city or recent_city
            requested = requested or recent_range
        city = city or context.profile.get("location")
        if not city:
            return ToolResult(
                "Which city should I check? I can remember it for future forecasts."
            )

        if requested:
            start, end = requested
            today = date.today()
            if end < today:
                return ToolResult(
                    f"Those dates have passed. This tool provides forecasts, not historical weather, for {city}."
                )
            if (end - today).days > 15:
                available_on = end - timedelta(days=15)
                label = f"{start.strftime('%b %-d')} to {end.strftime('%b %-d')}"
                return ToolResult(
                    f"A reliable daily forecast for {city} from {label} is not available yet. "
                    f"Curie’s live forecast window is 16 days. Ask again on or after "
                    f"{available_on.strftime('%b %-d')} for the complete range; I won’t substitute "
                    "your home location or invent conditions.",
                    data={
                        "city": city,
                        "start_date": start.isoformat(),
                        "end_date": end.isoformat(),
                        "available_on": available_on.isoformat(),
                    },
                    source="Open-Meteo",
                )
            forecast = await get_weather_range(city, start, end)
            days = list(forecast["days"])
            lows = [float(day["low"]) for day in days]
            highs = [float(day["high"]) for day in days]
            rain_chance = max(
                int(day.get("precipitation_probability") or 0) for day in days
            )
            tip = " Bring a light jacket." if min(lows) < 16 else ""
            if rain_chance >= 40:
                tip += " Pack an umbrella."
            text = (
                f"{forecast['city']} forecast for {start.strftime('%b %-d')} to "
                f"{end.strftime('%b %-d')}: lows {round(min(lows))}–{round(max(lows))}°C, "
                f"highs {round(min(highs))}–{round(max(highs))}°C, with up to "
                f"{rain_chance}% precipitation chance.{tip} Source: Open-Meteo."
            )
            return ToolResult(text=text, data=forecast, source=forecast.get("source"))

        weather = await get_weather(
            city, day_offset=1 if "tomorrow" in query.lower() else 0
        )
        preferences = " ".join(
            str(context.profile.get(key, ""))
            for key in (
                "jacket_preference",
                "temperature_preference",
                "clothing_preferences",
            )
        ).strip()
        tip = " ".join(weather.get("tips", []))
        if preferences:
            tip += f" I also considered your saved preference: {preferences}."
        if re.search(r"\bhumid(?:ity)?\b", query, re.IGNORECASE):
            humidity = weather.get("relative_humidity")
            if humidity is None:
                text = (
                    f"Live humidity for {weather['city']} is unavailable from the current "
                    f"weather response. Conditions are {weather['description'].lower()} at "
                    f"{weather['temperature']}°C."
                )
            else:
                text = (
                    f"Current humidity in {weather['city']} is {humidity}%, with "
                    f"{weather['description'].lower()} and {weather['temperature']}°C. "
                    f"Source: {weather.get('source', 'the weather provider')}."
                )
        elif re.search(r"\brain(?:ing)?\b", query, re.IGNORECASE):
            answer = "Yes" if weather.get("is_raining") else "No"
            source = weather.get("source", "the weather provider")
            text = f"{answer}. Current conditions from {source} show {weather['description'].lower()} in {weather['city']} at {weather['temperature']}°C. {tip}".strip()
        else:
            text = f"Weather for {weather['city']}: {weather['description']}, {weather['temperature']}°C. {tip}".strip()
        return ToolResult(text=text, data=weather, source=weather.get("source"))
