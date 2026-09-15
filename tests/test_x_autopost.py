from datetime import date, datetime, timezone
import random
from unittest.mock import patch

import pytest

from memory import local_store
from services.x_autopost import (
    HARD_DAILY_CAP,
    XAutopostConfig,
    XAutopostService,
    build_autopost_prompt,
    build_daily_schedule,
    validate_autopost_text,
)


def _config(**overrides):
    values = {
        "enabled": True,
        "live": False,
        "owner_id": "owner-1",
        "topics": ("science", "creative technology"),
        "timezone_name": "UTC",
        "window_start_hour": 8,
        "window_end_hour": 23,
        "daily_max": HARD_DAILY_CAP,
        "check_interval_seconds": 60,
        "startup_delay_seconds": 0,
    }
    values.update(overrides)
    return XAutopostConfig(**values)


def test_daily_schedule_is_randomized_but_evenly_spread_and_hard_capped():
    config = _config()
    schedule = build_daily_schedule(date(2026, 9, 15), config, rng=random.Random(7))

    assert len(schedule) == HARD_DAILY_CAP
    assert schedule == sorted(schedule)
    assert schedule[0].hour == 8
    assert schedule[-1].hour == 22
    assert all(item.tzinfo == timezone.utc for item in schedule)


@pytest.mark.parametrize(
    "text",
    (
        "Buy this coin now.",
        "A guaranteed profit, obviously.",
        "Breaking: the market is up.",
        "Hello @someone",
        "A thought #trending",
        "Read https://example.com",
    ),
)
def test_unattended_posts_reject_advice_mentions_tags_links_and_live_claims(text):
    with pytest.raises(ValueError):
        validate_autopost_text(text)


def test_unattended_posts_reject_near_duplicates():
    with pytest.raises(ValueError, match="too similar"):
        validate_autopost_text(
            "Curiosity is a small engine for large discoveries.",
            ["Curiosity is the small engine behind large discoveries."],
        )


def test_live_mode_requires_connected_x_account():
    config = _config(live=True)
    assert config.blockers(account_connected=False) == ["x_account_not_connected"]


def test_autopost_prompt_uses_complete_active_curie_persona():
    prompt = build_autopost_prompt(
        "science",
        ["A prior observation."],
    )

    assert "[ACTIVE PERSONA: Curie]" in prompt
    assert "complete active personality remains in force" in prompt
    assert "public X post" in prompt
    assert "light French identity" in prompt
    assert "operator-approved topic: science" in prompt
    assert "A prior observation." in prompt


@pytest.mark.asyncio
async def test_dry_run_processes_only_one_overdue_slot_and_persists_state(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    service = XAutopostService(_config())
    with patch(
        "llm.providers.ask_best_provider",
        return_value="Curiosity works best when it is allowed to revise its favorite answer.",
    ):
        result = await service.check_once(
            now=datetime(2026, 9, 15, 23, 30, tzinfo=timezone.utc)
        )

    assert result["status"] == "previewed"
    state = local_store.list_personal_items("owner-1", "x_autopost_state")[-1]
    statuses = [slot["status"] for slot in state["schedule"]]
    assert statuses.count("previewed") == 1
    assert statuses.count("missed") == HARD_DAILY_CAP - 1
    assert state["posted_count"] == 1


@pytest.mark.asyncio
async def test_live_connected_mode_publishes_and_records_post_id(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    service = XAutopostService(_config(live=True, daily_max=1))

    with (
        patch(
            "services.credential_vault.get_credential",
            return_value={"connected": True},
        ),
        patch.object(
            service,
            "_generate",
            return_value="Curiosity is disciplined wonder, with better notes. Voilà.",
        ),
        patch(
            "connectors.twitter.create_post",
            return_value={"data": {"id": "post-42"}},
        ) as create_post,
    ):
        result = await service.check_once(
            now=datetime(2026, 9, 15, 23, 30, tzinfo=timezone.utc)
        )

    assert result == {
        "status": "posted",
        "post_id": "post-42",
        "text": "Curiosity is disciplined wonder, with better notes. Voilà.",
    }
    create_post.assert_awaited_once()
    state = local_store.list_personal_items("owner-1", "x_autopost_state")[-1]
    assert state["posted_count"] == 1
    assert state["schedule"][0]["status"] == "posted"
    assert state["schedule"][0]["post_id"] == "post-42"
