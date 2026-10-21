"""Provider-backed flight status, live position, and map links."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

_FLIGHT = re.compile(r"^[A-Z0-9]{2,3}\s?\d{1,4}[A-Z]?$", re.I)
_OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/"
    "protocol/openid-connect/token"
)
_OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
_AVIATIONSTACK_URL = "https://api.aviationstack.com/v1/flights"
_token: tuple[str, float] | None = None


def normalize_flight_number(value: str) -> str:
    flight = re.sub(r"\s+", "", str(value)).upper()
    if not _FLIGHT.fullmatch(flight):
        raise ValueError("Use a flight number such as CX101 or BA 28")
    return flight


def tracking_links(flight: str) -> dict[str, str]:
    encoded = quote_plus(flight)
    return {
        "google": f"https://www.google.com/search?q={encoded}+flight+status",
        "flightaware": f"https://www.flightaware.com/live/flight/{encoded}",
        "flightradar24": f"https://www.flightradar24.com/data/flights/{flight.casefold()}",
    }


def _point_map(latitude: float, longitude: float) -> str:
    return (
        "https://www.google.com/maps/search/?api=1&query="
        f"{float(latitude):.6f}%2C{float(longitude):.6f}"
    )


async def _opensky_headers(client: httpx.AsyncClient) -> dict[str, str]:
    global _token
    client_id = os.getenv("OPENSKY_CLIENT_ID", "").strip()
    client_secret = os.getenv("OPENSKY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return {}
    now = time.time()
    if _token and _token[1] > now + 30:
        return {"Authorization": f"Bearer {_token[0]}"}
    response = await client.post(
        _OPENSKY_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    response.raise_for_status()
    value = response.json()
    _token = (str(value["access_token"]), now + int(value.get("expires_in", 1800)))
    return {"Authorization": f"Bearer {_token[0]}"}


async def _live_position(flight: str) -> dict[str, Any] | None:
    """OpenSky callsigns often match the operating flight number, but not always."""
    if os.getenv("OPENSKY_LIVE_TRACKING", "false").casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    async with httpx.AsyncClient(timeout=25) as client:
        headers = await _opensky_headers(client)
        response = await client.get(_OPENSKY_STATES_URL, headers=headers)
        response.raise_for_status()
        payload = response.json()
    wanted = flight.replace(" ", "").casefold()
    row = next(
        (
            state
            for state in payload.get("states") or []
            if str(state[1] or "").strip().replace(" ", "").casefold() == wanted
        ),
        None,
    )
    if not row:
        return None
    longitude, latitude = row[5], row[6]
    return {
        "icao24": row[0],
        "callsign": str(row[1] or "").strip(),
        "origin_country": row[2],
        "position_time": (
            datetime.fromtimestamp(row[3], timezone.utc).isoformat() if row[3] else None
        ),
        "last_contact": datetime.fromtimestamp(row[4], timezone.utc).isoformat(),
        "longitude": longitude,
        "latitude": latitude,
        "barometric_altitude_m": row[7],
        "on_ground": row[8],
        "speed_mps": row[9],
        "heading_degrees": row[10],
        "vertical_rate_mps": row[11],
        "geometric_altitude_m": row[13],
        "map_url": (
            _point_map(latitude, longitude)
            if latitude is not None and longitude is not None
            else None
        ),
        "source": "OpenSky Network",
        "fresh_at": datetime.now(timezone.utc).isoformat(),
        "untrusted": True,
    }


def _airport(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: raw.get(key)
        for key in (
            "airport",
            "timezone",
            "iata",
            "icao",
            "terminal",
            "gate",
            "delay",
            "scheduled",
            "estimated",
            "actual",
        )
        if raw.get(key) is not None
    }


async def _commercial_status(flight: str, date: str | None) -> list[dict[str, Any]]:
    api_key = os.getenv("AVIATIONSTACK_API_KEY", "").strip()
    if not api_key:
        return []
    params: dict[str, Any] = {
        "access_key": api_key,
        "flight_iata": flight,
        "limit": 5,
    }
    if date:
        params["flight_date"] = date
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(_AVIATIONSTACK_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    if payload.get("error"):
        raise RuntimeError(
            str((payload.get("error") or {}).get("message") or "Flight provider error")
        )
    rows = []
    for raw in payload.get("data") or []:
        aircraft = raw.get("aircraft") or {}
        live = raw.get("live") or {}
        rows.append(
            {
                "flight_date": raw.get("flight_date"),
                "status": raw.get("flight_status"),
                "airline": (raw.get("airline") or {}).get("name"),
                "flight": raw.get("flight") or {},
                "departure": _airport(raw.get("departure") or {}),
                "arrival": _airport(raw.get("arrival") or {}),
                "aircraft": {
                    key: aircraft.get(key)
                    for key in ("registration", "iata", "icao", "icao24")
                    if aircraft.get(key) is not None
                },
                "provider_live": {
                    key: live.get(key)
                    for key in (
                        "updated",
                        "latitude",
                        "longitude",
                        "altitude",
                        "direction",
                        "speed_horizontal",
                        "speed_vertical",
                        "is_ground",
                    )
                    if live.get(key) is not None
                },
                "source": "aviationstack",
                "fresh_at": datetime.now(timezone.utc).isoformat(),
                "untrusted": True,
            }
        )
    return rows


async def track_flight(
    flight_number: str, *, date: str | None = None
) -> dict[str, Any]:
    flight = normalize_flight_number(flight_number)
    if date:
        datetime.strptime(date, "%Y-%m-%d")
    status = await _commercial_status(flight, date)
    live = await _live_position(flight)
    return {
        "flight_number": flight,
        "requested_date": date,
        "status": status,
        "live_position": live,
        "links": tracking_links(flight),
        "commercial_provider_configured": bool(os.getenv("AVIATIONSTACK_API_KEY")),
        "opensky_enabled": os.getenv("OPENSKY_LIVE_TRACKING", "false").casefold()
        in {"1", "true", "yes", "on"},
        "limitations": (
            "Live transponder callsigns can differ from passenger flight numbers; "
            "a missing OpenSky match does not mean the flight is not operating."
        ),
    }


__all__ = ["normalize_flight_number", "track_flight", "tracking_links"]
