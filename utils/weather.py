# utils/weather.py

import python_weather
import aiohttp
import os
import re

# You can cache the client if you want (best for large bots)
_client = None

async def get_weather(city: str, unit: str = "metric"):
    """
    Fetch weather info for a given city name.
    Returns: dict with temperature, description, recommendations.
    """
    global _client
    unit_choice = python_weather.METRIC if unit == "metric" else python_weather.IMPERIAL

    # Always create a new client (safe), or cache it for performance
    async with python_weather.Client(unit=unit_choice) as client:
        weather = await client.get(city)
        # temperature, status, etc.
        temp = weather.temperature
        desc = getattr(weather, "description", None) or "No description."
        # Recommendations
        tips = []
        if temp < 16:
            tips.append("It's cold, take a jacket.")
        elif temp > 30:
            tips.append("It's really hot, stay hydrated.")
        if "rain" in desc.lower():
            tips.append("Rain expected, bring an umbrella.")
        if "sun" in desc.lower() or "clear" in desc.lower():
            tips.append("It's sunny, wear sunglasses if going out.")
        # You can expand this logic!
        return {
            "city": city,
            "temperature": temp,
            "description": desc,
            "tips": tips
        }
        
def extract_city_from_message(message):
    # Try: "weather in Tokyo", "Is it raining in Paris?", etc.
    match = re.search(r"(?:weather|rain|forecast|temperature|hot|cold|humid|sunny)\s*(?:in|at|for)?\s*([A-Za-z\s\-]+)", message, re.I)
    if match:
        # Clean up city name (strip whitespace, etc.)
        return match.group(1).strip().title()
    return None

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
            # Add more as needed
            return " | ".join(signals) if signals else None