#!/usr/bin/env python3
"""
Tests for the Trip & Vacation Planning skill.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub heavy DB dependencies before importing the skill
for _mod in (
    "psycopg2",
    "psycopg2.extras",
    "psycopg2.extensions",
    "pymongo",
    "pymongo.collection",
    "pymongo.errors",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


from agent.skills.trip_planner import (  # noqa: E402
    is_trip_query,
    extract_trip_params,
    handle_trip_query,
)

# ------------------------------------------------------------------
# is_trip_query
# ------------------------------------------------------------------


class TestIsTripQuery:
    def test_plan_a_trip(self):
        assert is_trip_query("plan a trip to Paris for 5 days")

    def test_vacation(self):
        assert is_trip_query("help me plan a vacation to Bali")

    def test_holiday(self):
        assert is_trip_query("I want to take a holiday to Japan")

    def test_travel(self):
        assert is_trip_query("I'm planning to travel to New York next month")

    def test_itinerary(self):
        assert is_trip_query("give me an itinerary for Rome 7 days")

    def test_visit(self):
        assert is_trip_query("I want to visit Tokyo for 2 weeks")

    def test_packing(self):
        assert is_trip_query("what should I pack for a beach trip?")

    def test_unrelated(self):
        assert not is_trip_query("what's the weather today?")

    def test_unrelated_coding(self):
        assert not is_trip_query("help me debug this Python code")

    def test_empty(self):
        assert not is_trip_query("")


# ------------------------------------------------------------------
# extract_trip_params
# ------------------------------------------------------------------


class TestExtractTripParams:
    def test_destination_extracted(self):
        params = extract_trip_params("plan a trip to Paris for 5 days")
        assert params["destination"] is not None
        assert "paris" in params["destination"].lower()

    def test_duration_days(self):
        params = extract_trip_params("I want to visit Tokyo for 7 days")
        assert params["duration_days"] == 7

    def test_duration_weeks_converted_to_days(self):
        params = extract_trip_params("plan a 2 week vacation in Bali")
        assert params["duration_days"] == 14

    def test_budget_budget_tier(self):
        params = extract_trip_params("plan a budget trip to Barcelona")
        assert params["budget_tier"] == "budget"

    def test_luxury_budget_tier(self):
        params = extract_trip_params("plan a luxury vacation to Maldives")
        assert params["budget_tier"] == "luxury"

    def test_default_budget_tier(self):
        params = extract_trip_params("plan a trip to Rome for 3 days")
        assert params["budget_tier"] == "moderate"

    def test_packing_focus(self):
        params = extract_trip_params("what should I pack for a beach trip?")
        assert params["packing_focus"] is True

    def test_no_packing_focus_for_itinerary(self):
        params = extract_trip_params("plan a 5 day trip to Paris")
        assert params["packing_focus"] is False

    def test_missing_destination(self):
        params = extract_trip_params("plan a 3 day vacation")
        assert params["destination"] is None

    def test_missing_duration(self):
        params = extract_trip_params("plan a trip to London")
        assert params["duration_days"] is None


# ------------------------------------------------------------------
# handle_trip_query (async, with LLM mocked)
# ------------------------------------------------------------------


class TestHandleTripQueryAsync:
    @pytest.mark.asyncio
    async def test_returns_none_for_non_trip(self):
        result = await handle_trip_query("what time is it?")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_response_for_trip_with_mock_llm(self):
        fake_response = "Day 1: Arrive in Paris, visit the Eiffel Tower..."

        with patch("llm.providers.ask_best_provider", return_value=fake_response):
            result = await handle_trip_query("plan a trip to Paris for 5 days")

        assert result is not None
        assert "Paris" in result or "paris" in result.lower()

    @pytest.mark.asyncio
    async def test_packing_query_uses_packing_prompt(self):
        fake_response = "Clothing: T-shirts, shorts, sunscreen..."

        with patch("llm.providers.ask_best_provider", return_value=fake_response):
            result = await handle_trip_query("what should I pack for a beach trip?")

        assert result is not None
        assert "Packing" in result or "pack" in result.lower() or "Clothing" in result

    @pytest.mark.asyncio
    async def test_trip_response_has_header(self):
        fake_response = "Here is your itinerary..."

        with patch("llm.providers.ask_best_provider", return_value=fake_response):
            result = await handle_trip_query("plan a vacation to Tokyo for 7 days")

        assert result is not None
        assert "✈️" in result or "Tokyo" in result

    @pytest.mark.asyncio
    async def test_falls_back_to_local_llm_on_provider_failure(self):
        fake_response = "Here is a packing list..."

        with patch(
            "llm.providers.ask_best_provider", side_effect=Exception("No cloud")
        ):
            with patch("llm.manager.ask_llm", return_value=fake_response):
                result = await handle_trip_query("what should I pack for a ski trip?")

        assert result is not None
        assert "Sorry" not in result or "pack" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_sorry_on_total_failure(self):
        with patch("llm.providers.ask_best_provider", side_effect=Exception("fail")):
            with patch("llm.manager.ask_llm", return_value="[Error: something]"):
                result = await handle_trip_query("plan a trip to London")

        assert result is not None
        assert "Sorry" in result or "✈️" in result

    @pytest.mark.asyncio
    async def test_local_only_uses_compact_prompt(self):
        """When running in local-only mode the skill must pass a shorter prompt to
        the LLM so that more context-window space is available for the response."""
        captured = {}

        def fake_ask(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["max_tokens"] = kwargs.get("max_tokens")
            return "Day 1: Explore the city centre..."

        with patch("llm.providers.is_local_only", return_value=True):
            with patch("llm.providers.ask_best_provider", side_effect=fake_ask):
                result = await handle_trip_query("plan a trip to Rome for 3 days")

        assert result is not None
        # Compact prompt is shorter than verbose prompt
        assert (
            len(captured["prompt"]) < 700
        ), f"Compact prompt should be short; got {len(captured['prompt'])} chars"
        # No hardcoded token cap — max_tokens should be None (fully dynamic)
        assert (
            captured["max_tokens"] is None
        ), f"max_tokens should be None for dynamic allocation, got {captured['max_tokens']}"

    @pytest.mark.asyncio
    async def test_cloud_mode_uses_verbose_prompt(self):
        """When cloud providers are available the skill should use the verbose prompt."""
        captured = {}

        def fake_ask(prompt, **kwargs):
            captured["prompt"] = prompt
            captured["max_tokens"] = kwargs.get("max_tokens")
            return "Day 1: ..."

        with patch("llm.providers.is_local_only", return_value=False):
            with patch("llm.providers.ask_best_provider", side_effect=fake_ask):
                result = await handle_trip_query("plan a trip to Paris for 5 days")

        assert result is not None
        # Verbose prompt is longer than compact prompt
        assert (
            len(captured["prompt"]) > 400
        ), f"Verbose prompt should be long; got {len(captured['prompt'])} chars"
        # max_tokens is None — no cap applied
        assert captured["max_tokens"] is None

    @pytest.mark.asyncio
    async def test_no_hardcoded_token_limits(self):
        """ask_best_provider must be called with max_tokens=None for fully dynamic allocation."""
        captured = {}

        def fake_ask(prompt, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return "Here is your itinerary..."

        with patch("llm.providers.is_local_only", return_value=False):
            with patch("llm.providers.ask_best_provider", side_effect=fake_ask):
                await handle_trip_query("plan a trip to Tokyo for 7 days")

        assert captured.get("max_tokens") is None


def test_import_guard():
    """Confirm the trip_planner module can be imported without DB connections."""
    import agent.skills.trip_planner  # noqa: F401

    assert True
