#!/usr/bin/env python3
"""
Tests for llm/providers.py:  _is_simple_query, is_local_only, compute_response_budget.
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
    is_local_only,
    compute_response_budget,
    get_active_providers,
)


# ------------------------------------------------------------------
# _is_simple_query
# ------------------------------------------------------------------

class TestIsSimpleQuery:
    def test_greeting_is_simple(self):
        assert _is_simple_query("hi") is True

    def test_hello_is_simple(self):
        assert _is_simple_query("hello there") is True

    def test_how_are_you_is_simple(self):
        assert _is_simple_query("how are you?") is True

    def test_weather_short_is_simple(self):
        assert _is_simple_query("what's the weather?") is True

    def test_short_random_text_with_no_keywords_is_not_simple(self):
        # Short prompt with no simple keywords → NOT simple (keyword check required)
        assert _is_simple_query("plan a trip to Rome") is False

    def test_long_prompt_with_weather_keyword_is_not_simple(self):
        """A verbose trip planning prompt that mentions 'weather' must NOT be classified
        as simple — the keyword check only applies to short (≤30 words) prompts."""
        long_prompt = (
            "You are an experienced travel planner. Create a practical, day-by-day "
            "itinerary for Paris for 5 days. Include weather information, food tips, "
            "transport advice, safety notes, and a moderate budget breakdown. "
            "Be friendly and conversational like a well-travelled friend."
        )
        assert len(long_prompt.split()) > 30, "Prompt must be >30 words for this test"
        assert _is_simple_query(long_prompt) is False

    def test_long_prompt_with_date_keyword_is_not_simple(self):
        long_prompt = " ".join(["word"] * 35) + " what is the date"
        assert _is_simple_query(long_prompt) is False

    def test_empty_string_is_not_simple(self):
        # Empty prompt has 0 words, no keywords match → False
        assert _is_simple_query("") is False

    def test_thank_you_is_simple(self):
        assert _is_simple_query("thanks so much!") is True

    def test_goodbye_is_simple(self):
        assert _is_simple_query("bye for now") is True


# ------------------------------------------------------------------
# is_local_only
# ------------------------------------------------------------------

class TestIsLocalOnly:
    def test_local_only_when_only_llama(self):
        with patch("llm.providers.get_active_providers", return_value=["llama.cpp"]):
            assert is_local_only() is True

    def test_not_local_only_with_cloud_providers(self):
        with patch("llm.providers.get_active_providers", return_value=["anthropic", "llama.cpp"]):
            assert is_local_only() is False

    def test_not_local_only_with_only_cloud(self):
        with patch("llm.providers.get_active_providers", return_value=["openai"]):
            assert is_local_only() is False

    def test_not_local_only_when_no_providers(self):
        with patch("llm.providers.get_active_providers", return_value=[]):
            assert is_local_only() is False


# ------------------------------------------------------------------
# compute_response_budget
# ------------------------------------------------------------------

class TestComputeResponseBudget:
    def test_short_prompt_returns_max_cap(self):
        """A 5-word prompt should have plenty of room; result should equal max_cap."""
        result = compute_response_budget("hello how are you today", max_cap=512)
        assert result == 512

    def test_long_prompt_reduces_budget(self):
        """A 1500-word prompt should leave little room in a 2048-token window."""
        huge_prompt = " ".join(["word"] * 1500)
        result = compute_response_budget(huge_prompt, max_cap=512)
        # 1500 words × 1.3 ≈ 1950 tokens; 2048 - 1950 - 32 = 66 tokens available
        assert result < 512
        assert result >= 64  # never below the floor

    def test_floor_is_64(self):
        """Even an enormous prompt should return at least 64."""
        enormous_prompt = " ".join(["word"] * 3000)
        result = compute_response_budget(enormous_prompt, max_cap=512)
        assert result == 64

    def test_respects_max_cap(self):
        short_prompt = "hi"
        assert compute_response_budget(short_prompt, max_cap=256) == 256
        assert compute_response_budget(short_prompt, max_cap=100) == 100

    def test_with_larger_context_window(self):
        """Simulates a user who set LLM_CONTEXT_SIZE=4096."""
        short_prompt = "hello"
        with patch("llm.manager.MODEL_CONTEXT_SIZE", 4096):
            result = compute_response_budget(short_prompt, max_cap=768)
        assert result == 768
