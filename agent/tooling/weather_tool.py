"""Live current-weather and forecast presentation."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from agent.tooling.contracts import ToolContext, ToolResult

_LOCATION = re.compile(
    r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,80}?)"
    r"(?:\s+(?:right\s+now|now|today|tomorrow|this\s+(?:morning|evening)|next\s+\w+))?"
    r"[?.!,]*$",
    re.IGNORECASE,
)


class WeatherTool:
    name = "weather"
    read_only = True

    async def execute(self, params: Mapping[str, Any], context: ToolContext) -> ToolResult:
        from utils.weather import get_weather

        query = str(params.get("query", ""))
        match = _LOCATION.search(query)
        city = (match.group(1).strip() if match else None) or context.profile.get("location")
        if not city:
            return ToolResult("Which city should I check? I can remember it for future forecasts.")
        weather = await get_weather(city, day_offset=1 if "tomorrow" in query.lower() else 0)
        preferences = " ".join(
            str(context.profile.get(key, ""))
            for key in ("jacket_preference", "temperature_preference", "clothing_preferences")
        ).strip()
        tip = " ".join(weather.get("tips", []))
        if preferences:
            tip += f" I also considered your saved preference: {preferences}."
        if re.search(r"\brain(?:ing)?\b", query, re.IGNORECASE):
            answer = "Yes" if weather.get("is_raining") else "No"
            source = weather.get("source", "the weather provider")
            text = f"{answer}. Current conditions from {source} show {weather['description'].lower()} in {weather['city']} at {weather['temperature']}°C. {tip}".strip()
        else:
            text = f"Weather for {weather['city']}: {weather['description']}, {weather['temperature']}°C. {tip}".strip()
        return ToolResult(text=text, data=weather, source=weather.get("source"))
