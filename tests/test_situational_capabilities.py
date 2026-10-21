import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest

from agent.intent_router import classify_request
from services.flight_tracking import normalize_flight_number, track_flight
from services.places import maps_directions_url, maps_search_url, search_places
from services.voice_delivery import trained_voice_id, voice_reply_requested


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Track flight CX101", "flight_track"),
        ("Where is BA 28 right now?", "flight_track"),
        ("Find a coffee shop near Central", "places_search"),
        ("Show me the Curie dashboard status", "dashboard_snapshot"),
        ("What's on my calendar tomorrow?", "calendar_list"),
        ("What's the latest political news?", "research"),
    ],
)
def test_natural_situational_requests_route_to_live_capabilities(text, action):
    request = classify_request(text)
    assert request is not None
    assert request.action == action


def test_calendar_write_is_typed_and_requires_fresh_approval():
    request = classify_request(
        "/calendar create Dentist | 2026-10-01T09:00:00+08:00 | "
        "2026-10-01T10:00:00+08:00 | Asia/Hong_Kong | Central | Check-up"
    )
    assert request is not None
    assert request.action == "calendar_create"
    assert request.needs_approval is True
    assert request.params["summary"] == "Dentist"

    natural = classify_request(
        "Schedule a dentist appointment tomorrow at 9:30 am on my calendar for 90 minutes"
    )
    assert natural is not None
    assert natural.action == "calendar_create" and natural.needs_approval
    assert natural.params["summary"] == "dentist appointment"


def test_google_maps_urls_are_official_and_encoded():
    search = urlsplit(maps_search_url("coffee near Central"))
    directions = urlsplit(maps_directions_url("Queen's Road", origin="Home"))
    assert search.netloc == "www.google.com"
    assert parse_qs(search.query)["api"] == ["1"]
    assert parse_qs(search.query)["query"] == ["coffee near Central"]
    assert parse_qs(directions.query)["destination"] == ["Queen's Road"]
    assert parse_qs(directions.query)["origin"] == ["Home"]


def test_places_search_has_useful_keyless_fallback(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    result = asyncio.run(search_places("post office near Central"))
    assert result["live_details"] is False
    assert result["places"] == []
    assert "google.com/maps/search" in result["search_url"]


def test_flight_tracking_is_bounded_and_has_live_links(monkeypatch):
    monkeypatch.delenv("AVIATIONSTACK_API_KEY", raising=False)
    monkeypatch.delenv("OPENSKY_LIVE_TRACKING", raising=False)
    result = asyncio.run(track_flight("cx 101"))
    assert result["flight_number"] == "CX101"
    assert result["status"] == []
    assert "flightaware.com" in result["links"]["flightaware"]
    with pytest.raises(ValueError):
        normalize_flight_number("not a flight")


def test_voice_requests_and_personality_assignments_are_explicit():
    assert voice_reply_requested("Please send that as a voice message")
    assert voice_reply_requested("Reply by audio")
    assert not voice_reply_requested("Tell me how voice synthesis works")
    assert trained_voice_id({"name": "Curie"}) == "curie"
    assert (
        trained_voice_id({"name": "Curie", "voice": {"trained_id": "laboratory-curie"}})
        == "laboratory-curie"
    )
