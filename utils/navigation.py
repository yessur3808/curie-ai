# utils/navigation.py
"""
Navigation utilities: geocoding via Nominatim (OpenStreetMap) and routing via OSRM.
Supports walking, cycling, and driving.  Transit routing requires an optional
OpenRouteService API key (ORS_API_KEY env var).
Optional real-time traffic data via TomTom API (TOMTOM_API_KEY env var).
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any, List

import httpx

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

# Optional API keys for enhanced features
ORS_API_KEY = os.getenv("ORS_API_KEY", "")
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY", "")

# OSRM public API (no key required)
OSRM_BASE_URL = "http://router.project-osrm.org/route/v1"

# OpenStreetMap Nominatim (no key required)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# OpenRouteService (key required for full support, free tier available)
ORS_BASE_URL = "https://api.openrouteservice.org/v2/directions"

# TomTom Traffic API (key required)
TOMTOM_TRAFFIC_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

# Mode aliases → OSRM profile
_OSRM_PROFILES: Dict[str, str] = {
    "drive": "driving",
    "driving": "driving",
    "car": "driving",
    "auto": "driving",
    "walk": "foot",
    "walking": "foot",
    "foot": "foot",
    "pedestrian": "foot",
    "hike": "foot",
    "bike": "cycling",
    "cycling": "cycling",
    "bicycle": "cycling",
    "cycle": "cycling",
}

# Mode aliases → ORS profile (transit falls back to driving-car since
# ORS free-tier does not include a public-transit profile; set ORS_API_KEY
# and upgrade to an ORS plan with pt profile for real transit routing)
_ORS_PROFILES: Dict[str, str] = {
    "transit": "driving-car",
    "bus": "driving-car",
    "train": "driving-car",
    "public": "driving-car",
    "public transit": "driving-car",
    "public transport": "driving-car",
}

# Human-readable mode labels
_MODE_LABELS: Dict[str, str] = {
    "driving": "🚗 Driving",
    "foot": "🚶 Walking",
    "cycling": "🚲 Cycling",
    "transit": "🚌 Public Transit",
}

_REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_HEADERS = {"User-Agent": "CurieAI-NavigationBot/1.0 (https://github.com/yessur3808/curie-ai)"}


# ------------------------------------------------------------------
# Geocoding
# ------------------------------------------------------------------

async def geocode(location: str) -> Optional[Dict[str, Any]]:
    """
    Convert a place name to geographic coordinates using Nominatim.

    Returns:
        {"lat": float, "lon": float, "display_name": str} or None on failure.
    """
    params = {
        "q": location,
        "format": "json",
        "limit": 1,
        "addressdetails": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(NOMINATIM_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                logger.debug(f"Nominatim returned no results for: {location}")
                return None
            item = data[0]
            return {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "display_name": item.get("display_name", location),
            }
    except Exception as exc:
        logger.warning(f"Geocoding failed for '{location}': {exc}")
        return None


# ------------------------------------------------------------------
# Routing via OSRM
# ------------------------------------------------------------------

async def get_osrm_route(
    origin_coords: Dict[str, float],
    dest_coords: Dict[str, float],
    profile: str = "driving",
    alternatives: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Get route(s) from OSRM between two coordinate pairs.

    Returns parsed route data or None on failure.
    """
    coords = (
        f"{origin_coords['lon']},{origin_coords['lat']};"
        f"{dest_coords['lon']},{dest_coords['lat']}"
    )
    url = f"{OSRM_BASE_URL}/{profile}/{coords}"
    params: Dict[str, Any] = {
        "steps": "true",
        "overview": "false",
        "annotations": "false",
    }
    if alternatives:
        params["alternatives"] = "true"

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                logger.debug(f"OSRM returned no routes: {data.get('code')}")
                return None
            return data
    except Exception as exc:
        logger.warning(f"OSRM routing failed ({profile}): {exc}")
        return None


# ------------------------------------------------------------------
# Routing via OpenRouteService (transit / additional modes)
# ------------------------------------------------------------------

async def get_ors_route(
    origin_coords: Dict[str, float],
    dest_coords: Dict[str, float],
    profile: str = "driving-car",
) -> Optional[Dict[str, Any]]:
    """
    Get route from OpenRouteService.  Requires ORS_API_KEY env var.

    Returns parsed route data or None.
    """
    if not ORS_API_KEY:
        return None

    url = f"{ORS_BASE_URL}/{profile}"
    payload = {
        "coordinates": [
            [origin_coords["lon"], origin_coords["lat"]],
            [dest_coords["lon"], dest_coords["lat"]],
        ],
        "instructions": True,
        "alternative_routes": {"target_count": 2, "weight_factor": 1.4},
    }
    headers = {**_HEADERS, "Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.warning(f"ORS routing failed ({profile}): {exc}")
        return None


# ------------------------------------------------------------------
# Traffic data via TomTom
# ------------------------------------------------------------------

async def get_traffic_info(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """
    Fetch real-time traffic flow data for a point.  Requires TOMTOM_API_KEY.

    Returns a dict with speed/freeflow info or None.
    """
    if not TOMTOM_API_KEY:
        return None

    params = {
        "point": f"{lat},{lon}",
        "unit": "KMPH",
        "key": TOMTOM_API_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, headers=_HEADERS) as client:
            resp = await client.get(TOMTOM_TRAFFIC_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            fd = data.get("flowSegmentData", {})
            return {
                "current_speed_kmh": fd.get("currentSpeed"),
                "free_flow_speed_kmh": fd.get("freeFlowSpeed"),
                "current_travel_time_s": fd.get("currentTravelTime"),
                "free_flow_travel_time_s": fd.get("freeFlowTravelTime"),
                "confidence": fd.get("confidence"),
            }
    except Exception as exc:
        logger.warning(f"TomTom traffic request failed: {exc}")
        return None


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """Format a duration in seconds into a human-readable string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} sec"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    mins = minutes % 60
    if mins:
        return f"{hours} hr {mins} min"
    return f"{hours} hr"


def format_distance(meters: float) -> str:
    """Format a distance in meters into a human-readable string."""
    if meters < 1000:
        return f"{int(meters)} m"
    km = meters / 1000
    if km < 10:
        return f"{km:.1f} km"
    return f"{int(km)} km"


def extract_steps(route: Dict[str, Any], max_steps: int = 8) -> List[str]:
    """
    Extract turn-by-turn directions from an OSRM route object.
    Returns at most *max_steps* human-readable strings.
    """
    steps: List[str] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            maneuver = step.get("maneuver", {})
            maneuver_type = maneuver.get("type", "")
            modifier = maneuver.get("modifier", "")
            name = step.get("name", "")
            distance = format_distance(step.get("distance", 0))

            if maneuver_type == "depart":
                direction = f"Head {modifier}" if modifier else "Depart"
                steps.append(f"{direction} on {name} for {distance}")
            elif maneuver_type == "arrive":
                steps.append("Arrive at destination")
            elif maneuver_type in ("turn", "new name", "on ramp", "off ramp"):
                verb = "Turn" if maneuver_type == "turn" else "Continue"
                if modifier:
                    verb = f"{verb} {modifier}"
                line = f"{verb}"
                if name:
                    line += f" onto {name}"
                line += f" ({distance})"
                steps.append(line)
            elif maneuver_type in ("roundabout", "rotary"):
                exit_num = maneuver.get("exit", "")
                line = f"Take roundabout exit {exit_num}"
                if name:
                    line += f" onto {name}"
                line += f" ({distance})"
                steps.append(line)

            if len(steps) >= max_steps:
                return steps
    return steps


# ------------------------------------------------------------------
# High-level routing helper
# ------------------------------------------------------------------

async def route(
    origin: str,
    destination: str,
    mode: str = "driving",
) -> Dict[str, Any]:
    """
    Top-level routing function: geocode both endpoints and fetch route.

    Args:
        origin:      Origin place name.
        destination: Destination place name.
        mode:        Transport mode string (e.g. 'drive', 'walk', 'bike', 'transit').

    Returns:
        A dict with keys:
            origin_name, destination_name, mode_label,
            routes (list of {distance_m, duration_s, steps}),
            traffic (optional),
            error (str, only on failure)
    """
    # Resolve mode → profile
    mode_lower = mode.lower().strip()
    osrm_profile = _OSRM_PROFILES.get(mode_lower)
    ors_profile = _ORS_PROFILES.get(mode_lower)
    use_transit = ors_profile is not None
    mode_label = _MODE_LABELS.get(
        "transit" if use_transit else (osrm_profile or "driving"),
        mode.capitalize(),
    )

    # Geocode both locations concurrently
    origin_geo, dest_geo = await asyncio.gather(geocode(origin), geocode(destination))

    if origin_geo is None:
        return {"error": f"Could not find location: {origin}"}
    if dest_geo is None:
        return {"error": f"Could not find location: {destination}"}

    # Fetch route data
    raw_data: Optional[Dict[str, Any]] = None
    if use_transit:
        raw_data = await get_ors_route(origin_geo, dest_geo, profile=ors_profile or "driving-car")
        if raw_data is None:
            # Fall back to OSRM driving if ORS not available
            mode_label = _MODE_LABELS["driving"] + " (transit N/A – no ORS key)"
            raw_data = await get_osrm_route(origin_geo, dest_geo, profile="driving")
    else:
        profile = osrm_profile or "driving"
        raw_data = await get_osrm_route(origin_geo, dest_geo, profile=profile)

    if raw_data is None:
        return {
            "error": (
                f"Could not calculate route from '{origin}' to '{destination}'. "
                "The routing service may be unavailable."
            )
        }

    # Parse routes
    routes: List[Dict[str, Any]] = []

    # Helper to parse OSRM-shaped responses into our internal route format
    def _parse_osrm_routes(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        parsed: List[Dict[str, Any]] = []
        for route in raw.get("routes", []):
            steps = extract_steps(route)
            parsed.append({
                "distance_m": route.get("distance", 0),
                "duration_s": route.get("duration", 0),
                "steps": steps,
            })
        return parsed

    # If this is an ORS (transit) response, parse its GeoJSON-like structure.
    # ORS v2/directions typically returns:
    # { "type": "FeatureCollection", "features": [ { "properties": { "summary": {...}, "segments": [...] }, ... } ] }
    if use_transit and isinstance(raw_data, dict) and raw_data.get("features"):
        for feature in raw_data.get("features", []):
            properties = feature.get("properties", {}) or {}
            summary = properties.get("summary", {}) or {}
            distance = summary.get("distance", 0)
            duration = summary.get("duration", 0)

            # Flatten all segment steps into a single list of step dicts
            step_list: List[Dict[str, Any]] = []
            for segment in properties.get("segments", []) or []:
                for step in segment.get("steps", []) or []:
                    step_list.append({
                        "instruction": step.get("instruction"),
                        "distance": step.get("distance"),
                        "duration": step.get("duration"),
                        "name": step.get("name"),
                        "type": step.get("type"),
                    })

            routes.append({
                "distance_m": distance,
                "duration_s": duration,
                "steps": step_list,
            })
    else:
        # Default: assume an OSRM-shaped response
        routes = _parse_osrm_routes(raw_data)

    # If we attempted transit routing but could not parse any routes from ORS,
    # fall back to OSRM driving directions.
    if use_transit and not routes:
        logger.warning(
            "Transit routing via ORS returned no parsable routes; falling back to OSRM driving."
        )
        mode_label = _MODE_LABELS["driving"] + " (transit N/A – routing fallback)"
        raw_data = await get_osrm_route(origin_geo, dest_geo, profile="driving")
        if raw_data is not None:
            routes = _parse_osrm_routes(raw_data)

    # Optionally fetch traffic at the midpoint of the origin leg
    traffic = None
    if TOMTOM_API_KEY:
        mid_lat = (origin_geo["lat"] + dest_geo["lat"]) / 2
        mid_lon = (origin_geo["lon"] + dest_geo["lon"]) / 2
        traffic = await get_traffic_info(mid_lat, mid_lon)

    return {
        "origin_name": origin_geo["display_name"].split(",")[0].strip(),
        "destination_name": dest_geo["display_name"].split(",")[0].strip(),
        "mode_label": mode_label,
        "routes": routes,
        "traffic": traffic,
    }
