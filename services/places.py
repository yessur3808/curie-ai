"""Google Maps links and optional Places API (New) discovery."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import httpx

_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.googleMapsUri",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.regularOpeningHours.openNow",
        "places.primaryTypeDisplayName",
    )
)


def maps_search_url(query: str) -> str:
    return "https://www.google.com/maps/search/?" + urlencode(
        {"api": "1", "query": query.strip()[:500]}
    )


def maps_directions_url(
    destination: str, *, origin: str = "", travel_mode: str = "driving"
) -> str:
    params = {"api": "1", "destination": destination.strip()[:500]}
    if origin.strip():
        params["origin"] = origin.strip()[:500]
    if travel_mode in {"driving", "walking", "bicycling", "transit"}:
        params["travelmode"] = travel_mode
    return "https://www.google.com/maps/dir/?" + urlencode(params)


async def search_places(
    query: str,
    *,
    limit: int = 5,
    open_now: bool = False,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_meters: float = 5000,
) -> dict[str, Any]:
    """Search Google Places when configured, with a useful keyless fallback."""
    cleaned = " ".join(str(query).split())[:500]
    if not cleaned:
        raise ValueError("A place or category is required")
    result: dict[str, Any] = {
        "query": cleaned,
        "search_url": maps_search_url(cleaned),
        "places": [],
        "provider": "Google Maps URL",
        "live_details": False,
        "untrusted": True,
    }
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return result

    body: dict[str, Any] = {
        "textQuery": cleaned,
        "pageSize": max(1, min(int(limit), 10)),
    }
    if open_now:
        body["openNow"] = True
    if latitude is not None and longitude is not None:
        if not (-90 <= float(latitude) <= 90 and -180 <= float(longitude) <= 180):
            raise ValueError("Location coordinates are outside the valid range")
        body["locationBias"] = {
            "circle": {
                "center": {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                },
                "radius": max(100.0, min(float(radius_meters), 50_000.0)),
            }
        }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            _TEXT_SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": _FIELD_MASK,
            },
            json=body,
        )
        response.raise_for_status()
        payload = response.json()

    places = []
    for raw in payload.get("places", [])[: max(1, min(int(limit), 10))]:
        name = raw.get("displayName") or {}
        place_type = raw.get("primaryTypeDisplayName") or {}
        location = raw.get("location") or {}
        places.append(
            {
                "id": str(raw.get("id") or "")[:200],
                "name": str(name.get("text") or "Unknown place")[:200],
                "address": str(raw.get("formattedAddress") or "")[:500],
                "type": str(place_type.get("text") or "")[:100],
                "rating": raw.get("rating"),
                "rating_count": raw.get("userRatingCount"),
                "open_now": (raw.get("regularOpeningHours") or {}).get("openNow"),
                "phone": str(raw.get("nationalPhoneNumber") or "")[:80],
                "website": str(raw.get("websiteUri") or "")[:1000],
                "maps_url": str(raw.get("googleMapsUri") or "")[:1000]
                or maps_search_url(str(name.get("text") or cleaned)),
                "directions_url": maps_directions_url(
                    str(raw.get("formattedAddress") or name.get("text") or cleaned)
                ),
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "untrusted": True,
            }
        )
    result.update(
        {
            "places": places,
            "provider": "Google Places API (New)",
            "live_details": True,
        }
    )
    return result


__all__ = ["maps_directions_url", "maps_search_url", "search_places"]
