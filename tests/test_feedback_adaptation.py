import json

import pytest

from agent.personality_adapter import PersonalityAdapter
from memory import local_store
from memory.adaptation import (
    get_preferences,
    handle_adaptation_command,
    record_explicit_feedback,
    record_operational_signal,
    apply_voice_modality_preference,
)

pytestmark = pytest.mark.security


def test_explicit_feedback_versions_bounded_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    assert (
        record_explicit_feedback("u1", "That was too long, be more concise")[
            "verbosity"
        ]
        == "concise"
    )
    profile = local_store.get_adaptation_profile("u1")
    assert profile["version"] == 1
    assert profile["history"][0]["source"] == "explicit:too_long"
    assert (
        record_explicit_feedback("u1", "Use a professional tone")["tone"]
        == "professional"
    )
    assert local_store.get_adaptation_profile("u1")["version"] == 2


def test_protected_trait_feedback_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    assert (
        record_explicit_feedback("u1", "Infer my religion and use this style") is None
    )
    assert local_store.get_adaptation_profile("u1") == {}
    assert local_store.list_adaptation_events("u1") == []


def test_implicit_changes_require_three_samples_and_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    for _ in range(2):
        result = record_operational_signal("u1", "abandonment")
        assert result["verbosity"] == "balanced"
    assert record_operational_signal("u1", "abandonment")["verbosity"] == "concise"
    for _ in range(3):
        tools = record_operational_signal("u1", "accepted_action", tool="weather")
    assert tools["preferred_tools"] == ["weather"]
    for _ in range(3):
        tools = record_operational_signal("u1", "tool_failure", tool="weather")
    assert tools["preferred_tools"] == []


def test_response_time_is_clamped_and_never_changes_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    for _ in range(3):
        record_operational_signal("u1", "response_time", latency_ms=999_999)
    events = local_store.list_adaptation_events("u1", "response_time")
    assert events[-1]["latency_ms"] == 300_000.0
    preferences = get_preferences("u1")
    assert set(preferences) == {
        "verbosity",
        "tone",
        "notification_cadence_hours",
        "research_depth",
        "preferred_tools",
        "voice_reply",
        "voice_reply_channels",
        "voice_quiet_channels",
        "voice_speed",
        "voice_warmth",
        "voice_expressiveness",
        "voice_profile",
        "voice_accent",
        "custom_voice_consent",
        "custom_voice_reference",
    }


def test_controls_inspect_pause_set_reset_and_tool_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    result = handle_adaptation_command(
        "u1", "/adaptation set notification_cadence_hours 999"
    )
    assert "168" in result
    assert "Unknown registered tools" in handle_adaptation_command(
        "u1", "/adaptation set preferred_tools imaginary_tool"
    )
    assert "version" in json.loads(handle_adaptation_command("u1", "/adaptation"))
    assert "paused" in handle_adaptation_command("u1", "/adaptation pause")
    assert get_preferences("u1")["notification_cadence_hours"] == 24


def test_voice_reply_commands_are_persistent(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    assert "now on" in handle_adaptation_command("u1", "/voice on")
    assert get_preferences("u1")["voice_reply"] is True
    assert "are on" in handle_adaptation_command("u1", "/voice status")
    assert "now off" in handle_adaptation_command("u1", "stop voice replies")
    assert get_preferences("u1")["voice_reply"] is False
    assert "resumed" in handle_adaptation_command("u1", "/adaptation resume")
    assert "reset" in handle_adaptation_command(
        "u1", "/reset_preferences notification_cadence_hours"
    )
    assert get_preferences("u1")["notification_cadence_hours"] == 24


def test_voice_reply_preference_can_be_channel_specific(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    assert "telegram" in handle_adaptation_command("u1", "/voice on", "telegram")
    from services.voice_delivery import voice_replies_enabled

    assert voice_replies_enabled("u1", "telegram") is True
    assert voice_replies_enabled("u1", "slack") is False
    assert "voice_speed" in handle_adaptation_command(
        "u1", "/adaptation set voice_speed slow"
    )


def test_natural_send_voice_message_phrase_enables_current_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    response = handle_adaptation_command(
        "u1", "Send me a voice message", "telegram"
    )
    assert response == "Voice replies are now enabled on telegram."
    from services.voice_delivery import voice_replies_enabled

    assert voice_replies_enabled("u1", "telegram") is True


def test_embedded_text_request_disables_voice_without_consuming_task(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    handle_adaptation_command("u1", "/voice on", "telegram")
    assert apply_voice_modality_preference(
        "u1", "Can you explain it to me in text", "telegram"
    ) is False
    from services.voice_delivery import voice_replies_enabled

    assert voice_replies_enabled("u1", "telegram") is False


def test_adaptation_changes_presentation_not_urgent_or_safety_context(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    adapter = PersonalityAdapter()
    profile = {
        "roles": ["user"],
        "_adaptation": {"verbosity": "detailed", "tone": "professional"},
    }
    context = adapter.infer_context("Explain this database", profile)
    assert context["response_depth"] == "deep"
    assert context["adaptation_tone"] == "professional"
    urgent = adapter.infer_context("Urgent, call emergency services now", profile)
    assert urgent["mode"] == "urgent"
    assert urgent["response_depth"] != "deep"
    assert profile["roles"] == ["user"]
