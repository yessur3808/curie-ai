#!/usr/bin/env python3
"""
Tests for llm/providers.py:
  _is_simple_query  — pure semantic routing, NO word-count threshold
  _COMPLEX_PATTERNS — complex task detection
  is_local_only     — local-only mode detection
  compute_response_budget — dynamic token allocation
"""

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Stub heavy dependencies before import
for _mod in ("psycopg2", "psycopg2.extras", "psycopg2.extensions",
             "pymongo", "pymongo.collection", "pymongo.errors"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from llm.providers import (  # noqa: E402
    _is_simple_query,
    _COMPLEX_PATTERNS,
    is_local_only,
    compute_response_budget,
)


# ------------------------------------------------------------------
# _is_simple_query  (semantic-only, no word-count threshold)
# ------------------------------------------------------------------

class TestIsSimpleQuery:
    # --- Trivially simple phrases that should be routed to local first ---

    def test_greeting_hi_is_simple(self):
        assert _is_simple_query("hi") is True

    def test_greeting_hello_is_simple(self):
        assert _is_simple_query("hello") is True

    def test_how_are_you_is_simple(self):
        assert _is_simple_query("how are you?") is True

    def test_weather_short_is_simple(self):
        assert _is_simple_query("what's the weather?") is True

    def test_weather_today_is_simple(self):
        assert _is_simple_query("what's the weather today?") is True

    def test_thanks_is_simple(self):
        assert _is_simple_query("thanks") is True

    def test_thank_you_is_simple(self):
        assert _is_simple_query("thank you") is True

    def test_goodbye_is_simple(self):
        assert _is_simple_query("goodbye") is True

    def test_good_morning_is_simple(self):
        assert _is_simple_query("good morning") is True

    def test_what_time_is_it_is_simple(self):
        assert _is_simple_query("what's the time?") is True

    def test_ok_is_simple(self):
        assert _is_simple_query("ok") is True

    # --- Complex task phrases that must NOT be routed to local-first ---

    def test_plan_trip_is_not_simple(self):
        """'plan a trip' is 3 words but COMPLEX — no word count check applies."""
        assert _is_simple_query("plan a trip to Rome") is False

    def test_remind_me_is_not_simple(self):
        assert _is_simple_query("remind me to call mom") is False

    def test_schedule_is_not_simple(self):
        assert _is_simple_query("schedule a meeting for tomorrow") is False

    def test_write_code_is_not_simple(self):
        assert _is_simple_query("write a Python function to sort a list") is False

    def test_vacation_is_not_simple(self):
        assert _is_simple_query("plan my vacation") is False

    def test_debug_is_not_simple(self):
        assert _is_simple_query("debug this code") is False

    def test_analyze_is_not_simple(self):
        assert _is_simple_query("analyze my spending habits") is False

    def test_long_prompt_with_itinerary_is_not_simple(self):
        """A verbose trip planning prompt containing 'weather' must NOT be simple.
        Previously this failed because word-count gating was absent for keywords."""
        long_prompt = (
            "You are an experienced travel planner. Create a practical, day-by-day "
            "itinerary for Paris for 5 days. Include weather information, food tips, "
            "transport advice, safety notes, and a moderate budget breakdown."
        )
        assert _is_simple_query(long_prompt) is False

    def test_reminder_in_longer_sentence_is_not_simple(self):
        assert _is_simple_query("can you set a reminder for me at 3pm?") is False

    def test_empty_string_is_not_simple(self):
        assert _is_simple_query("") is False

    def test_hi_plus_complex_is_not_simple(self):
        """'hi, plan my trip' starts with a greeting but contains a complex task."""
        assert _is_simple_query("hi, plan my trip to Japan") is False


# ------------------------------------------------------------------
# _COMPLEX_PATTERNS — ensure complex task keywords are detected
# ------------------------------------------------------------------

class TestComplexPatterns:
    def _matches(self, text: str) -> bool:
        return bool(_COMPLEX_PATTERNS.search(text))

    def test_plan(self):
        assert self._matches("plan a trip")

    def test_schedule(self):
        assert self._matches("schedule a meeting")

    def test_remind(self):
        assert self._matches("remind me tomorrow")

    def test_itinerary(self):
        assert self._matches("create an itinerary for Rome")

    def test_vacation(self):
        assert self._matches("plan my vacation to Bali")

    def test_code(self):
        assert self._matches("write some code for me")

    def test_debug(self):
        assert self._matches("debug this Python script")

    def test_analyze(self):
        assert self._matches("analyze my data")

    def test_translate(self):
        assert self._matches("translate this to French")

    def test_route(self):
        assert self._matches("give me directions to the airport")

    def test_greeting_no_match(self):
        assert not self._matches("hi")

    def test_thanks_no_match(self):
        assert not self._matches("thanks")

    def test_ok_no_match(self):
        assert not self._matches("ok")


# ------------------------------------------------------------------
# is_local_only
# ------------------------------------------------------------------

class TestIsLocalOnly:
    def test_local_only_when_only_llama(self):
        with patch("llm.providers.get_active_providers", return_value=["llama.cpp"]):
            assert is_local_only() is True

    def test_not_local_only_with_cloud_plus_local(self):
        with patch("llm.providers.get_active_providers", return_value=["anthropic", "llama.cpp"]):
            assert is_local_only() is False

    def test_not_local_only_with_only_cloud(self):
        with patch("llm.providers.get_active_providers", return_value=["openai"]):
            assert is_local_only() is False

    def test_not_local_only_when_no_providers(self):
        with patch("llm.providers.get_active_providers", return_value=[]):
            assert is_local_only() is False

    def test_not_local_only_gemini_only(self):
        with patch("llm.providers.get_active_providers", return_value=["gemini"]):
            assert is_local_only() is False


# ------------------------------------------------------------------
# compute_response_budget — dynamic token allocation, no hard cap
# ------------------------------------------------------------------

class TestComputeResponseBudget:
    def test_no_cap_returns_full_available(self):
        """When max_cap=None the full available context space is returned."""
        result = compute_response_budget("hello", max_cap=None)
        # 2048 - (1 word × 1.3 ≈ 2 tokens) - 32 buffer ≈ 2014
        assert result >= 1800

    def test_short_prompt_with_cap_returns_cap(self):
        result = compute_response_budget("hello how are you today", max_cap=512)
        assert result == 512

    def test_long_prompt_reduces_budget(self):
        """A 1500-word prompt should leave little room in a 2048-token window."""
        huge_prompt = " ".join(["word"] * 1500)
        result = compute_response_budget(huge_prompt, max_cap=512)
        assert result < 512
        assert result >= 64  # never below the floor

    def test_floor_is_64(self):
        """Even an enormous prompt returns at least 64."""
        enormous_prompt = " ".join(["word"] * 3000)
        result = compute_response_budget(enormous_prompt, max_cap=512)
        assert result == 64

    def test_floor_applies_without_cap(self):
        enormous_prompt = " ".join(["word"] * 3000)
        result = compute_response_budget(enormous_prompt, max_cap=None)
        assert result == 64

    def test_respects_max_cap_integer(self):
        short_prompt = "hi"
        assert compute_response_budget(short_prompt, max_cap=256) == 256
        assert compute_response_budget(short_prompt, max_cap=100) == 100

    def test_larger_context_window(self):
        """Simulates LLM_CONTEXT_SIZE=4096."""
        short_prompt = "hello"
        with patch("llm.manager.MODEL_CONTEXT_SIZE", 4096):
            result = compute_response_budget(short_prompt, max_cap=None)
        assert result >= 3500  # almost the full window
