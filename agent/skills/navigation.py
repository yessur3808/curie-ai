# agent/skills/navigation.py
"""
Navigation & Traffic skill.

Handles:
  - Real-time traffic information
  - Route planning and turn-by-turn directions
  - Multi-modal routing (walk, bike, transit, drive)
  - Travel time estimates
  - Alternative route suggestions
  - Smart rerouting (compares alternatives automatically)
"""

import logging
import re
from typing import Optional, Dict, Any

from utils.navigation import (
    route,
    format_duration,
    format_distance,
    TOMTOM_API_KEY,
    ORS_API_KEY,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UI constants
# ---------------------------------------------------------------------------

_SEP_FULL = 36          # Width of full-route separator line
_SEP_TRAFFIC = 30       # Width of traffic-only separator line
# Speeds below this fraction of free-flow speed indicate notable delays
_TRAFFIC_SLOWDOWN_THRESHOLD = 0.75

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

_NAV_KEYWORDS = [
    "route", "direction", "navigate", "navigation", "how do i get",
    "how to get", "way to", "path to", "get to", "go to",
    "drive to", "walk to", "bike to", "cycle to",
    "travel to", "trip to", "journey to",
    "from .+ to ", "eta", "travel time", "how far", "how long",
    "traffic", "commute", "reroute", "detour", "bypass",
    "shortest route", "fastest route", "alternative route",
    "public transit", "bus route", "train route", "transit",
    "directions to", "directions from",
]

_MODE_KEYWORDS: Dict[str, str] = {
    "drive": "drive",
    "driving": "drive",
    "car": "drive",
    "walk": "walk",
    "walking": "walk",
    "on foot": "walk",
    "hike": "walk",
    "bike": "bike",
    "biking": "bike",
    "cycling": "bike",
    "cycle": "bike",
    "bicycle": "bike",
    "transit": "transit",
    "bus": "transit",
    "train": "transit",
    "subway": "transit",
    "metro": "transit",
    "public transit": "transit",
    "public transport": "transit",
}


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

def is_navigation_query(message: str) -> bool:
    """Return True if the message looks like a navigation / traffic request."""
    msg = message.lower()
    return any(re.search(kw, msg) for kw in _NAV_KEYWORDS)


def extract_navigation_params(message: str) -> Optional[Dict[str, Any]]:
    """
    Extract origin, destination, and transport mode from a natural language
    navigation request.

    Supports patterns like:
      - "directions from New York to Boston"
      - "route from home to work by bike"
      - "how do I get from Paris to Lyon driving"
      - "how long to walk to Central Park"
      - "navigate to the Eiffel Tower"
      - "traffic on I-95"
    """
    msg = message.strip()

    # --- Pattern 1: "from X to Y" (with optional mode) ---
    m = re.search(
        r"(?:from|starting at|starting from)\s+(.+?)\s+(?:to|toward|towards)\s+"
        r"(.+?)(?:\s+(?:by|via|using|on foot|driving|walking|cycling|biking).*)?$",
        msg,
        re.IGNORECASE,
    )
    if m:
        origin = _clean_location(m.group(1).strip())
        destination = _clean_location(m.group(2).strip())
        mode = _extract_mode(msg)
        return {"origin": origin, "destination": destination, "mode": mode}

    # --- Pattern 2: "directions/route to X from Y" ---
    m = re.search(
        r"(?:directions?|route|navigate?)\s+(?:from\s+(.+?)\s+)?to\s+(.+?)(?:\s+(?:by|via|using).*)?$",
        msg,
        re.IGNORECASE,
    )
    if m:
        origin = _clean_location((m.group(1) or "").strip())
        # If no explicit origin is provided, let higher-level logic / LLM
        # handle clarification instead of defaulting to "my location".
        if not origin:
            return None
        destination = _clean_location(m.group(2).strip())
        mode = _extract_mode(msg)
        return {"origin": origin, "destination": destination, "mode": mode}

    # --- Pattern 3: "how do I get to X from Y" or "how long to drive to X" ---
    m = re.search(
        r"(?:how\s+(?:do\s+i|can\s+i|long|far)\s+(?:get|to|drive|walk|cycle|travel|go)?"
        r"\s*(?:to|from)?)\s+(.+?)(?:\s+from\s+(.+?))?(?:\s+(?:by|via|using).*)?$",
        msg,
        re.IGNORECASE,
    )
    if m:
        destination = _clean_location((m.group(1) or "").strip())
        origin = (m.group(2) or "").strip()
        # If the user didn't specify a starting point, avoid fabricating one;
        # this allows a conversational fallback to ask for the origin.
        if not origin:
            return None
        mode = _extract_mode(msg)
        if destination:
            return {"origin": origin, "destination": destination, "mode": mode}

    # --- Pattern 4: traffic query "traffic on X" or "traffic near X" ---
    m = re.search(r"traffic\s+(?:on|near|in|around|at)\s+(.+?)$", msg, re.IGNORECASE)
    if m:
        location = _clean_location(m.group(1).strip())
        return {"origin": location, "destination": location, "mode": "drive", "traffic_only": True}

    return None


def _clean_location(text: str) -> str:
    """Remove trailing filler words from a parsed location string."""
    stop_words = re.compile(
        r"\s*(?:please|thanks?|quickly|fast|now|today|tonight|tomorrow|asap|by\s+\w+|via\s+\w+|using\s+\w+)\s*$",
        re.IGNORECASE,
    )
    text = stop_words.sub("", text).strip(" .,?!")
    return text


def _extract_mode(message: str) -> str:
    """Detect transport mode from the message, defaulting to 'drive'."""
    msg = message.lower()
    for kw, mode in _MODE_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", msg):
            return mode
    return "drive"


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

def _build_response(params: Dict[str, Any], result: Dict[str, Any]) -> str:
    """Format a routing result into a user-friendly message."""
    if "error" in result:
        return f"❌ Navigation Error: {result['error']}"

    origin = result.get("origin_name", params.get("origin", "Origin"))
    destination = result.get("destination_name", params.get("destination", "Destination"))
    mode_label = result.get("mode_label", "")
    routes = result.get("routes", [])
    traffic = result.get("traffic")

    if not routes:
        return f"❌ No routes found from {origin} to {destination}."

    lines = [f"🗺️ *Route: {origin} → {destination}*"]
    lines.append(f"{'━' * _SEP_FULL}")

    primary = routes[0]
    dist = format_distance(primary["distance_m"])
    dur = format_duration(primary["duration_s"])
    lines.append(f"{mode_label} | 📍 {dist} | ⏱️ ~{dur}")

    # Traffic info (only shown when traffic data was actually fetched)
    if traffic:
        cur_speed = traffic.get("current_speed_kmh")
        free_speed = traffic.get("free_flow_speed_kmh")
        if (
            cur_speed is not None
            and free_speed is not None
            and free_speed != 0
        ):
            if cur_speed < free_speed * _TRAFFIC_SLOWDOWN_THRESHOLD:
                lines.append(
                    f"\n🚦 *Traffic Alert*: Current speed {cur_speed} km/h "
                    f"(normal: {free_speed} km/h) – expect delays!"
                )
            else:
                lines.append(f"\n🟢 Traffic flowing normally (~{cur_speed} km/h)")

    # Turn-by-turn steps
    steps = primary.get("steps", [])
    if steps:
        lines.append("\n📌 *Directions:*")
        for i, step in enumerate(steps, 1):
            lines.append(f"  {i}. {step}")

    # Alternative routes
    if len(routes) > 1:
        lines.append("\n🔄 *Alternative Routes:*")
        for i, alt in enumerate(routes[1:], 1):
            alt_dist = format_distance(alt["distance_m"])
            alt_dur = format_duration(alt["duration_s"])
            lines.append(f"  • Route {i + 1}: {alt_dist} | ~{alt_dur}")

    # Transit note
    if "transit" in mode_label.lower() and not ORS_API_KEY:
        lines.append(
            "\n💡 For detailed public transit routing, set ORS_API_KEY "
            "(free at openrouteservice.org)"
        )

    lines.append(f"\n{'━' * _SEP_FULL}")
    return "\n".join(lines)


def _build_traffic_only_response(params: Dict[str, Any], result: Dict[str, Any]) -> str:
    """Format a traffic-only response."""
    location = params.get("origin", "the requested location")

    if "error" in result:
        return f"❌ Could not retrieve traffic data for {location}: {result['error']}"

    traffic = result.get("traffic")
    if not traffic:
        if not TOMTOM_API_KEY:
            return (
                f"🚦 Real-time traffic information for *{location}* is not available.\n"
                "To enable live traffic data, set your TOMTOM_API_KEY in the environment."
            )
        return f"🚦 No traffic data found for *{location}*."

    lines = [f"🚦 *Traffic Info: {location}*", "━" * _SEP_TRAFFIC]
    cur_speed = traffic.get("current_speed_kmh")
    free_speed = traffic.get("free_flow_speed_kmh")
    if cur_speed is not None:
        lines.append(f"Current speed: {cur_speed} km/h")
    if free_speed is not None:
        lines.append(f"Free-flow speed: {free_speed} km/h")
    # Only require that both speeds are present; zero is a valid value.
    if cur_speed is not None and free_speed is not None:
        if free_speed == 0:
            # Cannot compute a meaningful slowdown ratio without a non-zero free-flow speed.
            lines.append("⚠️ Traffic conditions unclear (free-flow speed unavailable).")
        else:
            ratio = cur_speed / free_speed
            if ratio < _TRAFFIC_SLOWDOWN_THRESHOLD / 2:
                lines.append("🔴 Heavy traffic – significant delays expected")
            elif ratio < _TRAFFIC_SLOWDOWN_THRESHOLD:
                lines.append("🟡 Moderate traffic – some delays")
            else:
                lines.append("🟢 Traffic flowing normally")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_navigation_query(message: str) -> Optional[str]:
    """
    Detect and handle a navigation / traffic query.

    Returns a formatted response string, or None if the message is not a
    navigation request.
    """
    if not is_navigation_query(message):
        return None

    params = extract_navigation_params(message)
    if params is None:
        return None

    # Require a valid destination
    destination = params.get("destination", "").strip()
    if not destination or destination in ("my location", "here"):
        return None

    try:
        result = await route(
            origin=params["origin"],
            destination=params["destination"],
            mode=params.get("mode", "drive"),
        )
    except Exception as exc:
        logger.error(f"Navigation route call failed: {exc}", exc_info=True)
        return (
            "❌ Navigation service encountered an error. "
            "Please try again or specify a more precise location."
        )

    if params.get("traffic_only"):
        return _build_traffic_only_response(params, result)

    return _build_response(params, result)
