from datetime import datetime, timedelta, timezone

from services.proactive_policy import (
    delivery_allowed,
    delivery_updates,
    handle_proactive_command,
    rank_candidates,
)


NOW = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)


def _profile(**updates):
    profile = {
        "proactive_messaging_enabled": True,
        "timezone": "UTC",
        "proactive_quiet_hours": {"start": 22, "end": 8},
        "proactive_daily_max": 2,
        "proactive_weekly_max": 7,
        "proactive_topic_cooldown_hours": 72,
    }
    profile.update(updates)
    return profile


def test_requires_explicit_opt_in_and_valid_timezone():
    assert delivery_allowed({}, "study", NOW) == (False, "not_opted_in")
    assert delivery_allowed(_profile(timezone="Not/AZone"), "study", NOW) == (
        False,
        "invalid_timezone",
    )


def test_quiet_hours_snooze_daily_and_weekly_limits():
    assert delivery_allowed(_profile(), "study", NOW.replace(hour=23))[1] == "quiet_hours"
    assert delivery_allowed(
        _profile(proactive_snoozed_until=(NOW + timedelta(hours=1)).isoformat()),
        "study",
        NOW,
    )[1] == "snoozed"
    recent = [(NOW - timedelta(hours=hour)).isoformat() for hour in (1, 2)]
    assert delivery_allowed(_profile(proactive_sent_at=recent), "study", NOW)[1] == "daily_limit"
    week = [(NOW - timedelta(days=day)).isoformat() for day in range(1, 8)]
    assert delivery_allowed(
        _profile(proactive_daily_max=10, proactive_sent_at=week), "study", NOW
    )[1] == "weekly_limit"


def test_topic_cooldown_grows_after_rejection():
    profile = _profile(
        proactive_topic_last_sent={"study": (NOW - timedelta(hours=100)).isoformat()},
        proactive_rejection_count=1,
    )
    assert delivery_allowed(profile, "study", NOW)[1] == "topic_cooldown"
    assert delivery_allowed(profile, "exercise", NOW) == (True, "allowed")


def test_delivery_updates_store_only_safe_reason_and_bounded_history():
    updates = delivery_updates(_profile(), "study", "Repeated routine evidence.", NOW)
    assert updates["proactive_last_reason"] == "Repeated routine evidence."
    assert updates["proactive_awaiting_response"] is True
    assert updates["proactive_topic_last_sent"]["study"] == NOW.isoformat()


def test_controls_enable_disable_snooze_settings_and_why(monkeypatch):
    stored = {}
    monkeypatch.setattr(
        "services.proactive_policy.UserManager.get_user_profile",
        lambda _user: {**_profile(), **stored, "proactive_last_reason": "A repeated routine suggested it."},
    )
    monkeypatch.setattr(
        "services.proactive_policy.UserManager.update_user_profile",
        lambda _user, values: stored.update(values),
    )
    assert "limits 2/day" in handle_proactive_command("u1", "/proactive")
    assert "disabled" in handle_proactive_command("u1", "/proactive disable")
    assert stored["proactive_messaging_enabled"] is False
    assert "enabled" in handle_proactive_command("u1", "/proactive enable")
    assert "snoozed until" in handle_proactive_command("u1", "/proactive snooze 1d")
    assert "repeated routine" in handle_proactive_command("u1", "why did you send this?")
    assert "sports" in handle_proactive_command("u1", "/proactive exclude sports")
    assert "telegram" in handle_proactive_command("u1", "/proactive channel telegram")


def test_candidates_are_grounded_ranked_and_excluded():
    ranked = rank_candidates([
        {"kind": "check_in", "topic": "general", "reason": "Opted-in interval", "confidence": .9, "urgency": .1, "usefulness": .2},
        {"kind": "deadline", "topic": "project", "reason": "Tracked deadline is near", "confidence": .9, "urgency": .9, "usefulness": .9},
        {"kind": "routine", "topic": "sports", "reason": "Repeated routine", "confidence": .95, "urgency": .2, "usefulness": .5},
        {"kind": "routine", "topic": "guess", "reason": "", "confidence": .99},
    ], {"proactive_avoid_topics": ["sports"]})
    assert [item["kind"] for item in ranked] == ["deadline", "check_in"]
    assert "urgency=" in ranked[0]["ranking_reason"]
