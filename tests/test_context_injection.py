#!/usr/bin/env python3
"""
Tests for date/time, location, and preferences injection in _build_structured_prompt.

Verifies that the assistant is always aware of:
- Current date and time (in the user's timezone)
- Location (from learned profile or DEFAULT_LOCATION fallback)
- User preferences (name, dietary, travel style, etc.)
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

# Import the module-level helpers we are testing
import agent.chat_workflow as _cw_module
from agent.chat_workflow import _select_relevant_facts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workflow(**persona_overrides):
    """Return a minimal ChatWorkflow instance with a mocked persona."""
    with patch("agent.chat_workflow.UserManager"), \
         patch("agent.chat_workflow.get_session_manager"):
        from agent.chat_workflow import ChatWorkflow
        persona = {"system_prompt": "You are a helpful assistant."}
        persona.update(persona_overrides)
        wf = ChatWorkflow.__new__(ChatWorkflow)
        wf.persona = persona
        # Minimal cache that always misses so we exercise the full code path
        from collections import OrderedDict
        from agent.chat_workflow import PromptCache
        wf.prompt_cache = PromptCache()
        wf.minimal_sanitization = True
        wf.enable_small_talk = False
        return wf


def _build(workflow, user_profile: dict, user_text: str = "hello") -> str:
    return workflow._build_structured_prompt(user_profile, [], user_text, internal_id="test-user")


# ---------------------------------------------------------------------------
# [USER CONTEXT] block — always present
# ---------------------------------------------------------------------------

class TestUserContextBlock:
    def test_context_block_present_with_empty_profile(self):
        """Even a brand-new user with no profile sees the [USER CONTEXT] block."""
        wf = _make_workflow()
        prompt = _build(wf, {})
        assert "[USER CONTEXT]" in prompt

    def test_current_date_always_present(self):
        wf = _make_workflow()
        prompt = _build(wf, {})
        assert "Current date:" in prompt

    def test_current_time_always_present(self):
        wf = _make_workflow()
        prompt = _build(wf, {})
        assert "Current time:" in prompt

    def test_timezone_always_present(self):
        wf = _make_workflow()
        prompt = _build(wf, {})
        assert "Timezone:" in prompt

    def test_time_source_always_present(self):
        """The prompt must say where the time came from (system clock / internet-verified)."""
        wf = _make_workflow()
        prompt = _build(wf, {})
        assert "Time source:" in prompt


# ---------------------------------------------------------------------------
# Timezone resolution order
# ---------------------------------------------------------------------------

class TestTimezoneResolution:
    def test_profile_timezone_takes_priority(self):
        """If user has a timezone in their profile it should be used."""
        wf = _make_workflow()
        prompt = _build(wf, {"timezone": "Asia/Tokyo"}, "what time is it?")
        assert "Asia/Tokyo" in prompt or "JST" in prompt

    def test_default_timezone_env_var_fallback(self):
        """When no timezone in profile, DEFAULT_TIMEZONE env var is used."""
        with patch.object(_cw_module, "_DEFAULT_TIMEZONE", "America/New_York"):
            wf = _make_workflow()
            prompt = _build(wf, {})
        assert "America/New_York" in prompt or "EST" in prompt or "EDT" in prompt

    def test_utc_is_ultimate_fallback(self):
        """When no profile timezone and DEFAULT_TIMEZONE is blank, UTC is used."""
        with patch.object(_cw_module, "_DEFAULT_TIMEZONE", ""):
            wf = _make_workflow()
            prompt = _build(wf, {})
        assert "UTC" in prompt

    def test_invalid_timezone_falls_back_to_utc(self):
        """An invalid timezone value never causes a crash; UTC is used instead."""
        wf = _make_workflow()
        prompt = _build(wf, {"timezone": "Not/AReal_Timezone"})
        assert "UTC" in prompt
        assert "[USER CONTEXT]" in prompt


# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------

class TestLocationResolution:
    def test_profile_location_shown(self):
        """If user has a location in their profile it appears in the prompt."""
        wf = _make_workflow()
        prompt = _build(wf, {"location": "Paris, France"}, "what should I do today?")
        assert "Paris, France" in prompt

    def test_default_location_env_var_fallback(self):
        """When no location in profile, DEFAULT_LOCATION env var is used."""
        with patch.object(_cw_module, "_DEFAULT_LOCATION", "London, UK"):
            wf = _make_workflow()
            prompt = _build(wf, {})
        assert "London, UK" in prompt

    def test_no_location_when_both_empty(self):
        """No Location line is added when both profile and DEFAULT_LOCATION are empty."""
        with patch.object(_cw_module, "_DEFAULT_LOCATION", ""):
            wf = _make_workflow()
            prompt = _build(wf, {})
        assert "Location:" not in prompt

    def test_location_not_duplicated(self):
        """Location from profile must appear only in [USER CONTEXT], not also in
        [VERIFIED FACTS ABOUT USER], to avoid repetition."""
        wf = _make_workflow()
        profile = {"location": "Berlin, Germany", "name": "Alice"}
        prompt = _build(wf, profile)
        assert prompt.count("Berlin, Germany") == 1


# ---------------------------------------------------------------------------
# User preferences injection
# ---------------------------------------------------------------------------

class TestPreferencesInjection:
    def test_name_is_injected(self):
        wf = _make_workflow()
        prompt = _build(wf, {"name": "Alice"})
        assert "Alice" in prompt

    def test_dietary_preferences_injected(self):
        wf = _make_workflow()
        prompt = _build(wf, {"dietary_preferences": "vegan"})
        assert "vegan" in prompt

    def test_travel_style_injected(self):
        wf = _make_workflow()
        prompt = _build(wf, {"travel_style": "budget"})
        assert "budget" in prompt

    def test_occupation_injected(self):
        wf = _make_workflow()
        prompt = _build(wf, {"occupation": "software engineer"})
        assert "software engineer" in prompt

    def test_interests_injected(self):
        wf = _make_workflow()
        prompt = _build(wf, {"interests": ["hiking", "cooking"]})
        assert "hiking" in prompt or "cooking" in prompt

    def test_empty_profile_no_facts_block(self):
        """A genuinely empty profile must not produce a [VERIFIED FACTS ABOUT USER] block."""
        wf = _make_workflow()
        prompt = _build(wf, {})
        assert "[VERIFIED FACTS ABOUT USER]" not in prompt

    def test_profile_with_only_contextkeys_no_duplicate_block(self):
        """A profile with only location/timezone should not produce a FACTS block."""
        wf = _make_workflow()
        with patch.object(_cw_module, "_DEFAULT_LOCATION", ""):
            prompt = _build(wf, {"timezone": "UTC", "location": "NYC"})
        assert "[VERIFIED FACTS ABOUT USER]" not in prompt


# ---------------------------------------------------------------------------
# _select_relevant_facts — critical keys always included
# ---------------------------------------------------------------------------

class TestSelectRelevantFacts:
    def test_name_always_returned(self):
        profile = {"name": "Bob", "unrelated_fact": "x"}
        result = _select_relevant_facts(profile, "what's the weather?")
        assert "name" in result

    def test_timezone_always_returned(self):
        profile = {"timezone": "America/Chicago", "unrelated_fact": "x"}
        result = _select_relevant_facts(profile, "what's up?")
        assert "timezone" in result

    def test_location_always_returned(self):
        profile = {"location": "Tokyo", "unrelated_fact": "x"}
        result = _select_relevant_facts(profile, "just chatting")
        assert "location" in result

    def test_dietary_always_returned(self):
        profile = {"dietary_preferences": "vegan", "unrelated_fact": "x"}
        result = _select_relevant_facts(profile, "hello")
        assert "dietary_preferences" in result

    def test_empty_profile_returns_empty(self):
        assert _select_relevant_facts({}, "hello") == {}
