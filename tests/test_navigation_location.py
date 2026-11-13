import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from services import navigation_conversation as conversation
from utils import transit_directions as provider


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    conversation._LOCATION.clear()
    conversation._PENDING.clear()
    monkeypatch.delenv("GOOGLE_ROUTES_API_KEY", raising=False)


@pytest.mark.parametrize(
    "text,mode",
    [
        ("I want to get to K11 Atelier", "transit"),
        ("I want to go to Healthy Village", "transit"),
        ("Walk to Central Station", "walk"),
        ("Directions to the airport by car", "drive"),
        ("How do I get to the airport?", "transit"),
    ],
)
def test_parse_intent_and_default_mode(text, mode):
    assert conversation.parse_request(text)["mode"] == mode


@pytest.mark.asyncio
async def test_destination_then_pin_and_isolation():
    reply = await conversation.handle_navigation(
        "one", 1, text="I want to go to K11 Atelier"
    )
    assert "share your Telegram location" in reply
    other = await conversation.handle_navigation(
        "two", 1, location={"latitude": 22.3, "longitude": 114.2}
    )
    assert "Where would you" in other
    other_chat = await conversation.handle_navigation(
        "one", 2, location={"latitude": 22.3, "longitude": 114.2}
    )
    assert "Where would you" in other_chat
    reply = await conversation.handle_navigation(
        "one", 1, location={"latitude": 22.3, "longitude": 114.2}
    )
    assert "K11 Atelier" in reply and "travelmode=transit" in reply
    assert "Detailed directions aren’t connected" in reply
    assert "• " in reply


@pytest.mark.asyncio
async def test_pin_then_destination_and_expiry(monkeypatch):
    await conversation.handle_navigation(
        "one", 1, location={"latitude": 22.3, "longitude": 114.2}
    )
    reply = await conversation.handle_navigation("one", 1, text="Central Station")
    assert "destination=Central+Station" in reply
    conversation._LOCATION.clear()
    reply = await conversation.handle_navigation("one", 1, text="walk to K11 Atelier")
    assert "share your Telegram location" in reply
    reply = await conversation.handle_navigation(
        "one", 1, location={"latitude": 22.3, "longitude": 114.2}
    )
    assert "travelmode=walking" in reply


@pytest.mark.asyncio
async def test_live_update_is_silent_and_cancel_clears():
    reply = await conversation.handle_navigation(
        "one", 1, location={"latitude": 22.3, "longitude": 114.2}, refresh_only=True
    )
    assert reply is None
    await conversation.handle_navigation("one", 1, text="forget my location")
    assert conversation._LOCATION.get("point", owner_id="one:1") is None
    assert await conversation.handle_navigation("one", 1, text="cancel") is None


@pytest.mark.asyncio
async def test_api_request_and_transit_steps(monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "test-key")
    captured = {}

    async def post(self, url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "routes": [
                    {
                        "duration": "900s",
                        "distanceMeters": 5000,
                        "legs": [
                            {
                                "steps": [
                                    {
                                        "travelMode": "WALK",
                                        "navigationInstruction": {
                                            "instructions": "Walk to Central Station"
                                        },
                                    },
                                    {
                                        "travelMode": "TRANSIT",
                                        "transitDetails": {
                                            "transitLine": {
                                                "nameShort": "Island Line",
                                                "vehicle": {"name": {"text": "MTR"}},
                                            },
                                            "headsign": "Chai Wan",
                                            "stopCount": 5,
                                            "stopDetails": {
                                                "departureStop": {"name": "Central"},
                                                "arrivalStop": {"name": "North Point"},
                                            },
                                            "localizedValues": {
                                                "departureTime": {
                                                    "time": {"text": "10:30"},
                                                    "timeZone": "Asia/Hong_Kong",
                                                }
                                            },
                                        },
                                    },
                                    {
                                        "travelMode": "WALK",
                                        "navigationInstruction": {
                                            "instructions": "Walk to your destination"
                                        },
                                    },
                                ]
                            }
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = await provider.directions(
        {"latitude": 22.3, "longitude": 114.2}, "Healthy Village"
    )
    assert captured["json"]["travelMode"] == "TRANSIT"
    assert captured["json"]["origin"]["location"]["latLng"]["latitude"] == 22.3
    text = provider.format_directions(result)
    assert "• Walk to Central Station" in text
    assert "MTR Island Line from Central towards Chai Wan" in text
    assert "get off at North Point after 5 stops" in text
    assert "10:30 Asia/Hong_Kong" in text
    assert "• Walk to your destination" in text


@pytest.mark.asyncio
async def test_provider_failure_never_substitutes_drive(monkeypatch):
    monkeypatch.setenv("GOOGLE_ROUTES_API_KEY", "test-key")
    monkeypatch.setattr(
        httpx.AsyncClient, "post", AsyncMock(side_effect=httpx.ConnectError("offline"))
    )
    result = await provider.directions("Central", "North Point")
    assert result["routes"] == []
    assert "travelmode=transit" in result["map_links"]["Google Maps"]
    assert "couldn’t retrieve" in result["unavailable"]


def test_reject_invalid_coordinates():
    with pytest.raises(ValueError):
        provider.waypoint({"latitude": float("nan"), "longitude": 114.2})


@pytest.mark.asyncio
async def test_telegram_location_handler_uses_shared_pin(monkeypatch):
    from types import SimpleNamespace
    from connectors import telegram

    monkeypatch.setattr(telegram, "get_internal_id", lambda *args: "test-owner")
    message = SimpleNamespace(
        chat_id=101,
        location=SimpleNamespace(latitude=22.3, longitude=114.2),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=1, username="test"),
        edited_message=None,
    )
    await conversation.handle_navigation(
        "test-owner", 101, text="I want to get to Healthy Village"
    )
    await telegram.handle_location(update, None)
    text = "\n".join(call.args[0] for call in message.reply_text.call_args_list)
    assert "Healthy Village" in text
    assert "travelmode=transit" in text
    assert "origin=22.3%2C114.2" in text
    assert all(
        call.kwargs.get("parse_mode") is None
        for call in message.reply_text.call_args_list
    )
