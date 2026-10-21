from datetime import datetime, timedelta, timezone
import threading
import time

import pytest

from memory import UserManager
from services.autonomous_learning import (
    AutonomousLearningService,
    cluster_news,
    confidence_for,
    derive_relevant_areas,
    derive_research_topics,
    due_news_slot,
    handle_autonomous_learning_command,
    list_findings,
    save_finding,
)


def _sources(count=3):
    domains = ["reuters.com", "apnews.com", "bbc.com", "who.int"]
    return [
        {
            "id": f"S{index + 1}",
            "url": f"https://{domain}/story-{index}",
            "domain": domain,
            "publisher": domain,
            "quality": 0.95,
        }
        for index, domain in enumerate(domains[:count])
    ]


def test_derives_relevant_topics_and_areas_without_sensitive_values():
    profile = {
        "location": "Hong Kong",
        "upcoming_trip_destination": "Los Angeles",
        "interests": ["AI", "space science", "password security token"],
        "projects": [{"name": "Curie"}, "home automation"],
        "autonomous_research_topics": ["AI", "battery technology"],
        "proactive_avoid_topics": ["home automation"],
    }

    assert derive_relevant_areas(profile) == ["Hong Kong", "Los Angeles"]
    assert derive_research_topics(profile) == [
        "AI",
        "battery technology",
        "space science",
        "Curie",
    ]


def test_news_schedule_runs_each_slot_only_once_in_local_timezone():
    profile = {
        "timezone": "Asia/Hong_Kong",
        "autonomous_news_times": ["08:30", "18:30"],
    }
    now = datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)  # 19:00 HKT
    assert due_news_slot(profile, {}, now) == "2026-09-26T08:30"
    state = {"completed_news_slots": ["2026-09-26T08:30"]}
    assert due_news_slot(profile, state, now) == "2026-09-26T18:30"
    state["completed_news_slots"].append("2026-09-26T18:30")
    assert due_news_slot(profile, state, now) is None


def test_confidence_requires_multiple_independent_reliable_sources():
    assert confidence_for(_sources(1), "Claim [S1]") < 0.65
    assert confidence_for(_sources(3), "Claim [S1] and confirmation [S2].") >= 0.8
    assert confidence_for(
        _sources(3), "Sources conflict and the result remains uncertain [S1] [S2]."
    ) < confidence_for(_sources(3), "Sources agree [S1] [S2].")


def test_news_clustering_groups_reports_about_the_same_development():
    items = [
        {"headline": "Major storm closes Hong Kong schools", "domain": "reuters.com"},
        {
            "headline": "Hong Kong schools close as major storm arrives",
            "domain": "bbc.com",
        },
        {
            "headline": "Scientists publish a new exoplanet catalogue",
            "domain": "nature.com",
        },
    ]
    clusters = cluster_news(items)
    assert len(clusters[0]) == 2
    assert {item["domain"] for item in clusters[0]} == {"reuters.com", "bbc.com"}


def test_saved_findings_include_confidence_expiration_and_source_links():
    now = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)
    result = save_finding(
        "owner-1",
        {
            "kind": "news",
            "topic": "Hong Kong weather",
            "summary": "Two agencies agree on the warning [S1] [S2].",
            "sources": _sources(2),
            "expires_at": (now + timedelta(hours=36)).isoformat(),
        },
        now,
    )

    assert result["confidence"] >= 0.72
    assert result["independent_source_count"] == 2
    assert result["expires_at"] == (now + timedelta(hours=36)).isoformat()
    assert all(source["url"].startswith("https://") for source in result["sources"])


@pytest.mark.asyncio
async def test_due_cycle_researches_regional_global_and_rotating_topic():
    async def fake_news(_query):
        return [
            {
                "title": "Shared development changes regional policy - Reuters",
                "headline": "Shared development changes regional policy",
                "summary": "A supported account.",
                "url": "https://news.google.com/articles/1",
                "publisher": "Reuters",
                "domain": "reuters.com",
                "published_at": "today",
            },
            {
                "title": "Regional policy changes after shared development - AP",
                "headline": "Regional policy changes after shared development",
                "summary": "Independent confirmation.",
                "url": "https://news.google.com/articles/2",
                "publisher": "AP",
                "domain": "apnews.com",
                "published_at": "today",
            },
        ]

    async def fake_synth(_query, _snippets):
        return "The development was independently reported [S1] [S2]."

    async def fake_research(_query):
        return {
            "answer": "A useful technical change was confirmed [S1] [S2] [S3].",
            "sources": _sources(3),
        }

    profile = {
        "autonomous_learning_enabled": True,
        "autonomous_news_enabled": True,
        "autonomous_topic_research_enabled": True,
        "timezone": "Asia/Hong_Kong",
        "location": "Hong Kong",
        "autonomous_research_topics": ["battery technology"],
        "autonomous_news_times": ["08:30", "18:30"],
    }
    service = AutonomousLearningService(
        researcher=fake_research,
        news_fetcher=fake_news,
        synthesizer=fake_synth,
    )
    now = datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)

    created = await service.run_due("owner-2", profile, now)

    assert {item["topic"] for item in created} == {
        "regional news",
        "global news",
        "battery technology",
    }
    assert all(item["expires_at"] for item in created)
    assert len(list_findings("owner-2")) == 3


@pytest.mark.asyncio
async def test_expired_finding_is_refreshed_and_superseded():
    owner = "owner-stale"
    now = datetime(2026, 9, 26, 11, 0, tzinfo=timezone.utc)
    old = save_finding(
        owner,
        {
            "kind": "topic_research",
            "topic": "robotics",
            "query": "recent robotics changes",
            "summary": "An older finding [S1] [S2].",
            "sources": _sources(2),
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
        },
        now - timedelta(days=15),
    )

    async def refreshed(_query):
        sources = _sources(3)
        for index, source in enumerate(sources):
            source["url"] = source["url"].replace("story", "updated")
            source["passage"] = f"updated evidence {index}"
        return {
            "answer": "A newer robotics result is now established [S1] [S2] [S3].",
            "sources": sources,
        }

    service = AutonomousLearningService(researcher=refreshed)
    created = await service.run_due(
        owner,
        {
            "autonomous_learning_enabled": True,
            "autonomous_news_enabled": False,
            "autonomous_topic_research_enabled": False,
            "timezone": "UTC",
        },
        now,
    )

    rows = list_findings(owner)
    old_row = next(row for row in rows if row["id"] == old["id"])
    assert len(created) == 1
    assert old_row["status"] == "superseded"
    assert created[0]["status"] == "active"
    assert created[0]["last_verified_at"] == now.isoformat()


def test_only_fresh_correlated_findings_can_be_shared_and_marked_once():
    owner = "owner-3"
    UserManager.update_user_profile(
        owner,
        {
            "timezone": "UTC",
            "autonomous_learning_enabled": True,
            "autonomous_learning_share_enabled": True,
            "autonomous_learning_daily_share_max": 1,
        },
    )
    now = datetime.now(timezone.utc)
    weak = save_finding(
        owner,
        {
            "topic": "uncorroborated",
            "summary": "One outlet said this.",
            "sources": _sources(1),
            "expires_at": (now + timedelta(days=1)).isoformat(),
        },
        now,
    )
    strong = save_finding(
        owner,
        {
            "topic": "useful discovery",
            "summary": "Independent sources confirmed a useful change [S1] [S2].",
            "sources": _sources(3),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "usefulness": 0.9,
        },
        now,
    )
    service = AutonomousLearningService()

    candidate = service.share_candidate(owner, now=now)

    assert candidate is not None
    assert candidate["finding_id"] == strong["id"]
    assert weak["id"] != strong["id"]
    assert "Sources:" in candidate["message"]
    service.mark_shared(owner, strong["id"], now)
    assert service.share_candidate(owner, now=now) is None


def test_autolearn_commands_control_opt_in_topics_and_refresh():
    owner = "owner-4"
    assert "twice daily" in handle_autonomous_learning_command(
        owner, "/autolearn enable"
    )
    assert UserManager.get_user_profile(owner)["autonomous_learning_enabled"] is True
    assert "Added" in handle_autonomous_learning_command(
        owner, "/autolearn topic add fusion energy"
    )
    assert (
        "fusion energy"
        in UserManager.get_user_profile(owner)["autonomous_research_topics"]
    )
    status = handle_autonomous_learning_command(owner, "/autolearn status")
    assert "08:30" in status and "18:30" in status
    assert "Queued" in handle_autonomous_learning_command(owner, "/autolearn refresh")
    assert "preserved" in handle_autonomous_learning_command(
        owner, "/autolearn disable"
    )


def test_research_worker_does_not_block_proactive_message_loop(monkeypatch):
    from services.proactive_messaging import ProactiveMessagingService

    finished = threading.Event()
    service = ProactiveMessagingService(workflow=None, connectors={})

    async def slow_research(_owner_id, profile=None):
        await __import__("asyncio").sleep(0.15)
        finished.set()
        return []

    monkeypatch.setattr(service._autonomous_learning, "run_due", slow_research)
    monkeypatch.setattr(
        UserManager,
        "get_user_profile",
        staticmethod(lambda _owner_id: {"autonomous_learning_enabled": True}),
    )

    started = time.perf_counter()
    service._run_autonomous_learning([{"internal_id": "owner-worker"}])
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert finished.wait(1.0)
