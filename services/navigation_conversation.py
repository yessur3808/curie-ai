"""Short-lived location/destination pairing, isolated by Telegram chat and user."""

from __future__ import annotations

import re
from utils.ttl_cache import TTLCache
from utils.transit_directions import directions, format_directions, waypoint

_LOCATION = TTLCache(
    900,
    512,
    name="telegram_navigation_location",
    owner_scope="user",
    invalidation_event="15 minutes, reset or process restart",
    sensitivity="sensitive",
)
_PENDING = TTLCache(
    900,
    512,
    name="telegram_navigation_pending",
    owner_scope="user",
    invalidation_event="15 minutes, reset or process restart",
    sensitivity="personal",
)


def parse_request(text):
    from agent.skills.navigation import extract_navigation_params, _extract_mode

    params = extract_navigation_params(text)
    if params and not params.get("traffic_only"):
        return params
    match = re.search(
        r"\b(?:directions? to|navigate to|(?:want|need|would like) to (?:get|go|travel) to|how (?:do|can) i get to|how to get to|take me to|(?:walk|drive|cycle|go|get|travel) to)\s+(.+)",
        text,
        re.I,
    )
    if not match:
        return None
    destination = re.sub(
        r"\s+(?:(?:by|using|via)\s+(?:public transport|public transit|bus|train|car|foot|bike)|on foot|walking|driving|please)\s*[?.!]*$",
        "",
        match.group(1),
        flags=re.I,
    ).strip(" ?.!")
    if not destination:
        return None
    return {"origin": "", "destination": destination, "mode": _extract_mode(text)}


def clear_navigation(owner, chat):
    _LOCATION.clear(owner_id=f"{owner}:{chat}")
    _PENDING.clear(owner_id=f"{owner}:{chat}")


async def handle_navigation(
    owner, chat, *, text=None, location=None, refresh_only=False
):
    scope = f"{owner}:{chat}"
    pending = _PENDING.get("request", owner_id=scope)
    if text and text.strip().lower() in {
        "cancel",
        "cancel directions",
        "forget my location",
        "/reset",
    }:
        if not pending and _LOCATION.get("point", owner_id=scope) is None:
            return None
        clear_navigation(owner, chat)
        return "Directions cancelled and your shared location cleared."
    if location is not None:
        waypoint(location)
        _LOCATION.set("point", dict(location), owner_id=scope)
        if refresh_only:
            return None
        if not pending or pending.get("awaiting_destination"):
            _PENDING.set("request", {"awaiting_destination": True}, owner_id=scope)
            return "Where would you like to go? I’ll use public transport unless you ask to walk or drive. Your location is kept for 15 minutes."
        params = pending
    else:
        params = parse_request(text or "")
        if params is None:
            if (
                pending
                and pending.get("awaiting_destination")
                and text
                and not text.startswith("/")
            ):
                from agent.skills.navigation import _extract_mode

                if "?" in text or len(text) > 200:
                    return None
                params = {
                    "origin": "",
                    "destination": text.strip(),
                    "mode": _extract_mode(text),
                }
            else:
                return None
    origin = params.get("origin")
    if not origin or origin.lower() in {
        "here",
        "my location",
        "current location",
        "my current location",
    }:
        origin = _LOCATION.get("point", owner_id=scope)
    if not origin:
        _PENDING.set("request", params, owner_id=scope)
        return f"Please share your Telegram location (attachment → Location) so I can plan your journey to {params['destination']}. I’ll use {params['mode']} mode."
    result = await directions(
        origin, params["destination"], params.get("mode", "transit")
    )
    _PENDING.clear(owner_id=scope)
    return format_directions(result)
