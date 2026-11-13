"""Behavioral matrices: preserve intent/parameters across equivalent wording."""

import asyncio

import pytest

from agent.intent_router import classify_request, resolve_request
from agent.routing import route_operational_request, route_request
from agent.kernel.understanding import analyze_turn


@pytest.mark.parametrize(
    "prefix",
    [
        "Don't",
        "Please do not",
        "Could you not",
        "Would you please not",
        "Please don't actually",
        "Can you please not",
        "I don't want you to",
        "I do not want you to",
        "Please don't ever",
    ],
)
@pytest.mark.parametrize(
    "action",
    [
        "check my calendar tomorrow",
        "check the weather in Paris",
        "turn off the desk lamp",
        "inspect my hardware",
        "retrieve my Gmail messages",
    ],
)
def test_prohibitions_never_plan_or_classify(prefix, action, monkeypatch):
    text = f"{prefix} {action}."

    async def forbidden(*args, **kwargs):
        pytest.fail("Prohibition reached model classifier")

    monkeypatch.setattr("agent.intent_router._ask_intent_model", forbidden)
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    assert classify_request(text) is None
    assert route_operational_request(text) is None
    assert asyncio.run(resolve_request(text)) is None
    assert asyncio.run(route_request(text)).intent == "conversation"
    assert not analyze_turn(
        text, text, owner_id="", platform="telegram"
    ).operational_decisions


@pytest.mark.parametrize(
    "prefix",
    [
        "How would you",
        "How do you",
        "Tell me how you",
        "Could you explain how you'd",
    ],
)
@pytest.mark.parametrize(
    "action",
    [
        "check my calendar tomorrow",
        "check the weather in Paris",
        "turn off the desk lamp",
    ],
)
def test_how_to_does_not_execute(prefix, action):
    text = f"{prefix} {action}?"
    assert classify_request(text) is None
    assert route_operational_request(text) is None
    assert not analyze_turn(
        text, text, owner_id="", platform="telegram"
    ).operational_decisions


@pytest.mark.parametrize(
    "text",
    [
        "Could you avoid checking my calendar?",
        "Please refrain from turning off the desk lamp.",
        "I would rather you didn't check my calendar.",
        "I'd rather you didn't check my calendar.",
        "I’d rather you didn’t check my calendar.",
    ],
)
def test_indirect_prohibitions_never_reach_model(text, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Prohibition reached model classifier")

    monkeypatch.setattr("agent.intent_router._ask_intent_model", forbidden)
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    assert classify_request(text) is None
    assert asyncio.run(resolve_request(text)) is None
    assert not analyze_turn(
        text, text, owner_id="", platform="telegram"
    ).operational_decisions


@pytest.mark.parametrize(
    "text",
    [
        "Please check whether the desk lamp isn't on.",
        "Could you check if the desk lamp is not on?",
        "Isn't the desk lamp on?",
        "Is the desk lamp not on?",
    ],
)
def test_negated_device_state_is_still_a_read(text):
    result = classify_request(text)
    assert result is not None
    assert result.action == "home_status"
    assert result.params["target"] == "desk lamp"


@pytest.mark.parametrize(
    "text",
    [
        "Please check if my desk lamp is currently not on.",
        "Is my desk lamp currently not on?",
        "Could you please check whether my desk lamp is off?",
        "Can you please see if my desk lamp is on?",
    ],
)
def test_polite_device_read_preserves_target(text):
    result = classify_request(text)
    assert result is not None
    assert result.action == "home_status"
    assert result.params["target"] == "desk lamp"


@pytest.mark.parametrize(
    "frame",
    [
        "Is the desk lamp {state}?",
        "Could you check whether the desk lamp is {state}?",
        "Can you see if my desk lamp is {state}?",
    ],
)
@pytest.mark.parametrize(
    "state", ["on", "off", "switched on", "switched off", "turned on", "turned off"]
)
def test_device_state_read_has_exact_target(frame, state):
    result = classify_request(frame.format(state=state))
    assert result.action == "home_status"
    assert result.params["target"] == "desk lamp"


@pytest.mark.parametrize(
    "place", ["NYC", "New York City", "Kyoto, Japan", "Ulaanbaatar"]
)
@pytest.mark.parametrize(
    "timing", ["today", "tomorrow", "from November 5 to November 7"]
)
def test_correct_full_location_span_preserves_time_and_question(place, timing):
    original = f"Could you check whether it will rain in {place} {timing}?"
    result = classify_request(
        "Actually I meant Osaka. Check that instead.",
        [{"role": "user", "content": original}],
    )
    assert result.action == "weather"
    assert result.params["query"] == original.replace(place, "Osaka")


def test_privacy_constraint_does_not_disable_public_weather():
    result = classify_request(
        "Could you check the weather in Osaka tomorrow, without using my location?"
    )
    assert result.action == "weather"


def test_correction_does_not_revive_weather_after_unrelated_topic():
    history = [
        {"role": "user", "content": "Check the weather in Paris tomorrow."},
        {"role": "user", "content": "New topic: help me name my novel."},
    ]
    assert (
        classify_request("Actually I meant Kyoto. Check that instead.", history) is None
    )


def test_chained_corrections_preserve_original_date():
    history = [{"role": "user", "content": "Check the weather in NYC tomorrow."}]
    for city in ("Kyoto", "Osaka", "Lisbon"):
        text = f"Actually I meant {city}. Check that instead."
        result = classify_request(text, history)
        assert result.params["query"] == f"Check the weather in {city} tomorrow."
        history.extend(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": "Forecast retrieved."},
            ]
        )
