from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.intent_router import classify_request
from agent.orchestration.response_policy import ResponsePolicy
from agent.skills.scheduler import is_reminder_query
from agent.tooling.contracts import ToolContext
from agent.tooling.weather_tool import WeatherTool
from contracts.catalog import handle_capabilities_command
from memory.learning import _extract_explicit_trip_facts
from services.proactive_messaging import ProactiveMessagingService


class _IdentityStyle:
    def apply_response_style(self, response, *_args, **_kwargs):
        return response


def _capability_report():
    return {
        "contract_version": "1.1.0",
        "status": "degraded",
        "runtime": {"speech": {"ready": True, "backend": "trained_voice"}},
        "capabilities": [
            {
                "name": "dashboard_snapshot",
                "display_name": "Dashboard Snapshot",
                "description": "I can read the live Curie dashboard directly.",
                "category": "skill",
                "available": True,
                "error": None,
            },
            {
                "name": "calendar_list",
                "display_name": "Calendar List",
                "description": "I can read Google Calendar.",
                "category": "skill",
                "available": False,
                "error": "Google Calendar is not connected for this account.",
            },
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        "What are your current capabilities?",
        "What can you do?",
        "What useful things can you help me with? Give me a short, clean list.",
        "What can you actually help me with from here? Not the marketing version, just the useful version.",
        "Ha, good. What can you actually help me with from here? Not the marketing version, just what is genuinely useful.",
        "Are all your tools still active?",
    ],
)
def test_natural_capability_questions_use_authoritative_discovery(text):
    with patch(
        "contracts.catalog.discover_capabilities", return_value=_capability_report()
    ):
        answer = handle_capabilities_command(text)
    assert any(
        heading in answer
        for heading in ("Current capabilities", "What is genuinely useful right now")
    )
    assert "dashboard" in answer.casefold()
    assert "Calendar List" in answer


def test_practical_capability_wording_returns_a_compact_useful_subset():
    with patch(
        "contracts.catalog.discover_capabilities", return_value=_capability_report()
    ):
        answer = handle_capabilities_command(
            "Ha, good. What can you actually help me with from here? Not the marketing version, just what is genuinely useful."
        )

    assert "59 active" not in answer
    assert "diagnose health" in answer
    assert "Calendar List" in answer


def test_capability_audit_and_top_three_use_authoritative_discovery():
    report = _capability_report()
    report["capabilities"].extend(
        [
            {
                "name": "home_control",
                "display_name": "Home Control",
                "description": "Control a uniquely resolved device.",
                "category": "skill",
                "available": True,
                "error": None,
            },
            {
                "name": "flight_track",
                "display_name": "Flight Track",
                "description": "Track a flight.",
                "category": "skill",
                "available": True,
                "error": None,
            },
        ]
    )
    with patch("contracts.catalog.discover_capabilities", return_value=report):
        audit = handle_capabilities_command(
            "Curie, capability audit. Simulation only: do not execute tools or change anything. In one short answer, what kinds of work can you genuinely help me with?"
        )
        top = handle_capabilities_command(
            "Which three capabilities are most useful today? Exactly three short bullets."
        )

    assert "Current capabilities" in audit
    assert len(top.splitlines()) == 3
    assert all(line.startswith("- ") for line in top.splitlines())


def test_available_calendar_tool_is_not_claimed_connected_without_a_live_check():
    report = _capability_report()
    report["capabilities"][1].update(available=True, error=None)
    with patch("contracts.catalog.discover_capabilities", return_value=report):
        answer = handle_capabilities_command(
            "Can you read my Google Calendar right now?"
        )

    assert "need to verify" in answer
    assert "your calendar is connected" not in answer.casefold()


def test_one_capability_and_safety_limit_use_authoritative_grounding():
    with patch(
        "contracts.catalog.discover_capabilities", return_value=_capability_report()
    ):
        capability = handle_capabilities_command(
            "Name one capability you genuinely have now that could save me time today."
        )
    limitation = handle_capabilities_command(
        "And one thing you cannot safely do without me?"
    )

    assert capability == "I can read the live Curie dashboard directly."
    assert "consequential external change" in limitation
    assert "approval" in limitation


def test_one_available_calendar_capability_does_not_claim_account_connectivity():
    report = _capability_report()
    report["capabilities"] = [
        {
            "name": "calendar_create",
            "display_name": "Calendar Create",
            "description": "Prepare a calendar event.",
            "category": "skill",
            "available": True,
            "error": None,
        }
    ]
    with patch("contracts.catalog.discover_capabilities", return_value=report):
        answer = handle_capabilities_command(
            "Name one capability you genuinely have now that could save me time today."
        )

    assert "prepare a calendar event" in answer.casefold()
    assert "connected calendar" not in answer.casefold()
    assert "verify access" in answer.casefold()


def test_device_reasoning_explanations_never_authorize_a_guess():
    plural = handle_capabilities_command(
        "Suppose I said, 'turn off both lights.' How would you work out which two I meant?"
    )
    missing = handle_capabilities_command(
        "If no device had that exact name, what should you do?"
    )
    low_confidence = handle_capabilities_command(
        "If your confidence in a device match were 72 percent, what would you do?"
    )

    assert "instead of guessing" in plural
    assert "leave every device unchanged" in missing
    assert "would not execute" in low_confidence
    assert "90%" in low_confidence


def test_natural_postposed_device_pronoun_is_treated_as_ambiguous_reasoning():
    response = handle_capabilities_command(
        "Suppose I say 'turn those off.' What does 'those' refer to, and when should you refuse to guess?"
    )

    assert "most recent verified device set" in response
    assert "instead of guessing" in response


def test_focused_capability_answers_are_truthful_and_direct():
    with patch(
        "contracts.catalog.discover_capabilities", return_value=_capability_report()
    ):
        dashboard = handle_capabilities_command(
            "Can you access the dashboard info directly?"
        )
        calendar = handle_capabilities_command("Do you have access to my calendar?")
        voice = handle_capabilities_command("Can you send voice messages?")
    assert dashboard.startswith("Yes.")
    assert "not connected" in calendar
    assert "trained_voice" in voice


def test_alarm_correction_is_not_misrouted_as_creation():
    assert not is_reminder_query(
        "But I am already awake, it does not make sense to set an alarm"
    )
    assert is_reminder_query("Set an alarm for 7am")


def test_explicit_web_search_routes_to_research():
    request = classify_request("Do a quick Google search and check their website")
    assert request is not None
    assert request.action == "research"


def test_trip_memory_handles_dates_and_short_contextual_answers():
    assert _extract_explicit_trip_facts(
        "I have a trip from 30sep to 9th of october"
    ) == {"upcoming_trip_dates": "30sep to 9th of october"}
    history = [("assistant", "Where are you going for the trip?")]
    assert _extract_explicit_trip_facts("Los Angeles", history) == {
        "upcoming_trip_destination": "Los Angeles"
    }
    purpose_history = [("user", "I have a trip from Sep 30 to Oct 9")]
    assert _extract_explicit_trip_facts(
        "It's for my girlfriend's childhood friend's wedding", purpose_history
    ) == {"upcoming_trip_purpose": "my girlfriend's childhood friend's wedding"}


def test_repeated_french_aside_is_removed_but_occasional_flair_survives():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    repeated = policy.finalize(
        "*Bon courage.* The lights are off.",
        "Turn off the lights",
        history=[("assistant", "*C'est vrai.* That makes sense.")],
    )
    occasional = policy.finalize(
        "*C'est charmant.* Nicely done.",
        "I finally solved that little puzzle",
        history=[],
    )
    assert repeated == "The lights are off."
    assert "C'est charmant" in occasional


def test_low_stakes_personal_update_drops_patronizing_advice_and_forced_french():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    result = policy.finalize(
        "That sounds like a solid way to end the day. You earned some rest after "
        "that long shift, so I won't suggest anything else just yet. *Bon repos*.",
        "I just finished work today at 6pm and had a late dinner.",
        history=[],
    )
    assert result == "Ah, got it. Hope dinner was good."


def test_live_style_personal_update_and_alarm_correction_stay_brief():
    policy = ResponsePolicy({"name": "Curie"}, _IdentityStyle())
    personal = policy.finalize(
        "That sounds like a long day, so you are probably tired now. It makes "
        "perfect sense to rest if your body is asking for it. Bon repos.",
        "I just finished work today at 6pm and had a late dinner.",
        history=[],
    )
    alarm = policy.finalize(
        "There is no need to set one then. You might want to check your calendar.",
        "But I am already awake, it does not make sense to set an alarm.",
        history=[],
    )
    assert personal == "Ah, got it. Hope dinner was good."
    assert (
        alarm
        == "Right. You’re already awake, so setting an alarm now wouldn’t make sense."
    )


@pytest.mark.asyncio
async def test_companion_proactive_mode_can_send_a_caring_message_without_a_topic():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {"role": "user", "content": "Turn off all lights"},
        {"role": "assistant", "content": "Done. Both lights are off."},
    ]
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={
                "proactive_predictions_enabled": True,
                "proactive_style": "companion",
            },
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch(
            "services.proactive_messaging.random.choice",
            side_effect=lambda options: options[0],
        ),
    ):
        assert (
            await service._generate_proactive_message("u1", "telegram")
            == "Hey, how are you doing today?"
        )


@pytest.mark.asyncio
async def test_today_rain_question_uses_daily_forecast(monkeypatch):
    async def fake_range(city, start, end):
        return {
            "city": "Hong Kong",
            "source": "Open-Meteo",
            "days": [
                {
                    "precipitation_probability": 70,
                    "rain_mm": 4.2,
                    "description": "Rain showers",
                }
            ],
        }

    async def fail_current(*_args, **_kwargs):
        raise AssertionError("a forward rain question must not use current conditions")

    monkeypatch.setattr("utils.weather.get_weather_range", fake_range)
    monkeypatch.setattr("utils.weather.get_weather", fail_current)
    result = await WeatherTool().execute(
        {"query": "Will it rain today in Hong Kong?"},
        ToolContext("u1", profile={"location": "Hong Kong"}),
    )
    assert "70%" in result.text
    assert "Rain is likely" in result.text


def test_startup_notification_has_safe_default_cooldown(tmp_path, monkeypatch):
    import connectors.telegram as telegram_connector

    marker = Path(tmp_path) / "startup-notified"
    marker.touch()
    monkeypatch.delenv("CURIE_STARTUP_NOTIFICATION_COOLDOWN_SECONDS", raising=False)
    monkeypatch.setenv("CURIE_STARTUP_NOTIFICATION_STATE", str(marker))
    assert not telegram_connector._startup_notification_due()
