"""Google Routes directions with explicit modes and an honest keyless fallback."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from urllib.parse import urlencode

import httpx

MODES = {"transit": "TRANSIT", "walk": "WALK", "drive": "DRIVE", "bike": "BICYCLE"}
LINK_MODES = {
    "transit": "transit",
    "walk": "walking",
    "drive": "driving",
    "bike": "bicycling",
}


def waypoint(value):
    if isinstance(value, dict):
        lat, lon = float(value["latitude"]), float(value["longitude"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid location coordinates")
        return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}
    return {"address": str(value)}


def maps_link(origin, destination, mode):
    start = (
        f"{origin['latitude']},{origin['longitude']}"
        if isinstance(origin, dict)
        else origin
    )
    return "https://www.google.com/maps/dir/?" + urlencode(
        {
            "api": "1",
            "origin": start,
            "destination": destination,
            "travelmode": LINK_MODES[mode],
        }
    )


async def directions(origin, destination, mode="transit"):
    if mode not in MODES:
        raise ValueError("Unsupported travel mode")
    payload = {
        "origin": waypoint(origin),
        "destination": waypoint(destination),
        "travelMode": MODES[mode],
        "languageCode": "en",
    }
    link = maps_link(origin, destination, mode)
    result = {
        "origin_name": "Your shared location" if isinstance(origin, dict) else origin,
        "destination_name": destination,
        "mode_label": {
            "transit": "Public transport",
            "walk": "Walking",
            "drive": "Driving",
            "bike": "Cycling",
        }[mode],
        "map_links": {"Google Maps": link},
        "routes": [],
        "source": "Google Maps",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    key = os.getenv("GOOGLE_ROUTES_API_KEY", "")
    if not key:
        return {
            **result,
            "unavailable": "Detailed directions aren’t connected yet. Open the route below for current directions.",
        }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                json=payload,
                headers={
                    "X-Goog-Api-Key": key,
                    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.legs.steps.navigationInstruction,routes.legs.steps.transitDetails,routes.legs.steps.travelMode,routes.warnings",
                },
            )
            response.raise_for_status()
            data = response.json()
        for route in data.get("routes", []):
            steps = []
            for leg in route.get("legs", []):
                for step in leg.get("steps", []):
                    transit = step.get("transitDetails")
                    if transit:
                        line = transit.get("transitLine", {})
                        vehicle = (
                            line.get("vehicle", {})
                            .get("name", {})
                            .get("text", "Transit")
                        )
                        name = line.get("nameShort") or line.get("name") or ""
                        stops = transit.get("stopDetails", {})
                        departure = stops.get("departureStop", {}).get(
                            "name", "departure stop"
                        )
                        arrival = stops.get("arrivalStop", {}).get(
                            "name", "arrival stop"
                        )
                        instruction = f"Take {vehicle} {name} from {departure}"
                        if transit.get("headsign"):
                            instruction += f" towards {transit['headsign']}"
                        local = transit.get("localizedValues", {}).get(
                            "departureTime", {}
                        )
                        time_text = local.get("time", {}).get("text")
                        if time_text:
                            instruction += (
                                f" (departure {time_text} {local.get('timeZone', '')})"
                            )
                        instruction += f"; get off at {arrival}"
                        if transit.get("stopCount"):
                            instruction += f" after {transit['stopCount']} stops"
                    else:
                        instruction = step.get("navigationInstruction", {}).get(
                            "instructions"
                        )
                    if instruction:
                        steps.append(instruction)
            if steps:
                result["routes"].append(
                    {
                        "steps": steps,
                        "distance_m": route.get("distanceMeters", 0),
                        "duration_s": float(route.get("duration", "0s").rstrip("s")),
                        "warnings": route.get("warnings", []),
                    }
                )
        if not result["routes"]:
            result["unavailable"] = (
                "No detailed route was returned for this journey. Check the route in Maps below."
            )
        return result
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # Do not expose request headers, keys, or precise coordinates in logs/errors.
        return {
            **result,
            "routes": [],
            "unavailable": "I couldn’t retrieve current directions. Try again or open the route below.",
        }


def format_directions(result):
    from utils.navigation import format_distance, format_duration

    lines = [
        f"{result['origin_name']} → {result['destination_name']}",
        f"• Travel mode: {result['mode_label']}",
    ]
    if result.get("unavailable"):
        lines.append("• " + result["unavailable"])
    elif result["routes"]:
        route = result["routes"][0]
        lines.append(
            f"• Estimated journey: {format_duration(route['duration_s'])}, {format_distance(route['distance_m'])}"
        )
        lines.extend("• " + step for step in route["steps"])
        lines.extend("• " + warning for warning in route.get("warnings", []))
        lines.append(
            "• Source: Google Maps. Journey estimates can change; check departures before leaving."
        )
    lines.append("• Open in Google Maps: " + result["map_links"]["Google Maps"])
    return "\n".join(lines)
