from datetime import datetime, timedelta, timezone

from services.proactive_concierge import build_concierge_candidates
from services.proactive_policy import rank_candidates

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


def _profile(**updates):
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_concierge_enabled": True,
        "proactive_categories": ["help", "assist", "ease", "fun", "companion"],
        "timezone": "UTC",
        "proactive_quiet_hours": {"start": 22, "end": 8},
        "proactive_daily_max": 5,
        "proactive_weekly_max": 35,
        "proactive_topic_cooldown_hours": 12,
    }
    profile.update(updates)
    return profile


def test_grounded_project_offer_outranks_generic_ease_and_fun():
    candidates = build_concierge_candidates(
        "owner-1",
        _profile(),
        [("user", "I'm working on a little home dashboard")],
        NOW,
    )

    assert {item["category"] for item in candidates} == {"help", "ease", "fun"}
    project = next(item for item in candidates if item["category"] == "help")
    assert project["topic"] == "project next steps"
    assert project["evidence"][0]["source"] == "recent_conversation"
    assert rank_candidates(candidates, _profile())[0]["topic"] == ("project next steps")


def test_category_preferences_can_keep_only_original_entertainment():
    profile = _profile(proactive_categories=["fun"])
    candidates = build_concierge_candidates("owner-1", profile, [], NOW)

    assert len(candidates) == 1
    assert candidates[0]["kind"] == "entertainment"
    assert candidates[0]["category"] == "fun"
    assert "original" in candidates[0]["reason"]


def test_concierge_is_explicit_opt_in_and_respects_topic_cooldowns():
    assert (
        build_concierge_candidates(
            "owner-1", {**_profile(), "proactive_concierge_enabled": False}, [], NOW
        )
        == []
    )

    first = build_concierge_candidates("owner-1", _profile(), [], NOW)
    ease = next(item for item in first if item["category"] == "ease")
    fun = next(item for item in first if item["category"] == "fun")
    profile = _profile(
        proactive_topic_last_sent={
            ease["topic"]: (NOW - timedelta(hours=1)).isoformat(),
            fun["topic"]: (NOW - timedelta(hours=1)).isoformat(),
        }
    )

    second = build_concierge_candidates("owner-1", profile, [], NOW)
    assert all(item["category"] not in {"ease", "fun"} for item in second)


def test_commands_and_acknowledgements_do_not_become_grounded_work():
    candidates = build_concierge_candidates(
        "owner-1",
        _profile(proactive_categories=["help", "assist"]),
        [("user", "Turn off all lights"), ("user", "Thanks")],
        NOW,
    )
    assert candidates == []
