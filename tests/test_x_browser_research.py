from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from connectors import x_browser as xb
from memory import UserManager
from memory.local_store import save_personal_item
from services import x_research as xr

PROFILE = {
    "timezone": "Asia/Hong_Kong",
    "x_research_enabled": True,
    "x_research_browser_risk_accepted": True,
    "x_research_handle": "CurieTest",
    "proactive_messaging_enabled": True,
    "proactive_daily_max": 10,
    "proactive_weekly_max": 50,
}
BASE = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services import resource_governor as rg

    monkeypatch.setattr(
        rg, "resource_governor", rg.ResourceGovernor(tmp_path / "budget.json")
    )
    monkeypatch.setattr(
        "services.self_diagnostics.diagnostic_monitor.snapshot", lambda **kw: {}
    )
    profiles = {"owner": dict(PROFILE)}
    monkeypatch.setattr(
        UserManager, "get_user_profile", lambda owner: profiles.get(owner, {})
    )
    monkeypatch.setattr(
        UserManager,
        "update_user_profile",
        lambda owner, fields: profiles.setdefault(owner, {}).update(fields),
    )
    monkeypatch.setattr(
        "memory.repositories.get_repositories",
        lambda: SimpleNamespace(
            identities=SimpleNamespace(get_external_id=lambda owner, platform: "42"),
            profiles=SimpleNamespace(
                list_with_identities=lambda: [{"internal_id": "owner"}]
            ),
        ),
    )
    return profiles


def due(owner="owner", index=0):
    return xr.daily_slots(owner, PROFILE, BASE)[index] + timedelta(minutes=1)


def post(
    now, number=1, text="Hong Kong schools close after major storm brings strong winds"
):
    return {
        "id": str(number),
        "url": f"https://x.com/Source/status/{number}",
        "text": text,
        "published_at": (now - timedelta(hours=1)).isoformat(),
        "author": "Source",
    }


def news(now):
    return [
        {
            "url": f"https://{domain}/news/storm",
            "domain": domain,
            "publisher": domain,
            "headline": "Hong Kong schools close after major storm brings strong winds",
            "published_at": now.isoformat(),
        }
        for domain in ("reuters.com", "bbc.com")
    ]


def worker(now, **kwargs):
    return xr.XResearchWorker(
        AsyncMock(return_value=True),
        collector=kwargs.pop("collector", AsyncMock(return_value=[post(now)])),
        news_fetcher=kwargs.pop("news_fetcher", AsyncMock(return_value=news(now))),
        metadata=kwargs.pop(
            "metadata", lambda owner: {"ready": True, "saved_at": "one"}
        ),
        **kwargs,
    )


def test_daily_schedule_is_stable_bounded_spaced_and_owner_scoped():
    counts = set()
    for offset in range(60):
        now = BASE + timedelta(days=offset)
        slots = xr.daily_slots("owner", PROFILE, now)
        counts.add(len(slots))
        assert 2 <= len(slots) <= 6
        assert slots == xr.daily_slots("owner", PROFILE, now)
        assert all(9 <= slot.hour < 22 for slot in slots)
        assert all((b - a).total_seconds() > 30 * 60 for a, b in zip(slots, slots[1:]))
    assert counts == {2, 3, 4, 5, 6}
    assert xr.daily_slots("other", PROFILE, BASE) != xr.daily_slots(
        "owner", PROFILE, BASE
    )


def test_no_catchup_burst_or_replay_and_no_quiet_hour_scan():
    now = due()
    index, stamp = xr.due_slot("owner", PROFILE, {}, now)
    assert (
        xr.due_slot("owner", PROFILE, {"used_slots": [stamp.isoformat()]}, now) is None
    )
    assert xr.due_slot("owner", PROFILE, {}, BASE) is None
    assert xr.due_slot("owner", PROFILE, {}, BASE + timedelta(hours=15)) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://api.x.com/2/users/me",
        "https://x.com/2/tweets",
        "http://x.com/home",
        "https://x.com.evil.test/",
        "https://x.com@evil.test/",
        "https://127.0.0.1/",
        "file:///etc/passwd",
        "https://x.com:8010/",
        "https://evil.test/",
    ],
)
def test_browser_never_routes_paid_api_or_arbitrary_hosts(url):
    assert not xb.allowed_resource(url)


def test_browser_can_render_normal_site_resources():
    assert xb.allowed_resource("https://x.com/home")
    assert xb.allowed_resource("https://abs.twimg.com/client-web/main.js")


def test_leads_filter_duplicates_stale_future_irrelevant_and_malformed_urls():
    now = due()
    good = post(now)
    entries = [
        good,
        good,
        post(now, 2),
        post(now - timedelta(days=4), 3),
        post(now + timedelta(days=4), 4),
        post(now, 5, "My lunch is nice and here is a lovely picture"),
        {**post(now, 6), "url": "https://evil.test/"},
    ]
    assert len(xr.select_leads(entries, {}, now)) == 1
    assert not xr.select_leads([good], {"seen_posts": [good["url"]]}, now)


def test_related_sources_require_fresh_relevant_distinct_reliable_publishers():
    now = due()
    items = news(now)
    assert len(xr.correlated_sources(post(now), items + items, now)) == 2
    assert not xr.correlated_sources(post(now), news(now - timedelta(days=7)), now)
    assert not xr.correlated_sources(
        post(now),
        [{**items[0], "headline": "A distant planet is discovered by a telescope"}],
        now,
    )
    assert not xr.correlated_sources(post(now), [{**items[0], "domain": "x.com"}], now)


@pytest.mark.asyncio
async def test_missing_server_login_does_not_browse_or_consume_slot(runtime):
    now = due()
    service = worker(now, metadata=lambda owner: {"ready": False})
    await service.run_owner("owner", now)
    service.collector.assert_not_awaited()
    assert xr.state_for("owner")["status"] == "server_browser_login_required"
    assert not xr.state_for("owner").get("used_slots")


@pytest.mark.asyncio
async def test_no_risk_acceptance_or_disabled_owner_means_no_browser(runtime):
    now = due()
    service = worker(now)
    runtime["owner"]["x_research_browser_risk_accepted"] = False
    await service.run_owner("owner", now)
    await service.run_owner("other", now)
    service.collector.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_scan_saves_scoped_expiring_sources_delivers_once_and_survives_restart(
    runtime,
):
    now = due()
    service = worker(now)
    await service.run_owner("owner", now)
    await worker(now).run_owner("owner", now)
    assert service.collector.await_count == service.send.await_count == 1
    assert len(xr.fresh_findings("owner", now)) == 1
    assert not xr.fresh_findings("other", now)
    assert not xr.fresh_findings("owner", now + timedelta(days=2))
    item = xr.fresh_findings("owner", now)[0]
    assert (
        item["confidence"] == 0.6
        and "not claim verification" in item["confidence_basis"]
    )
    assert "not a full fact-check" in service.send.call_args.args[2]
    assert xr.state_for("owner")["status"] == "completed"


@pytest.mark.asyncio
async def test_login_challenge_stops_later_sessions_until_manual_resume_or_new_login(
    runtime,
):
    now = due()
    collector = AsyncMock(side_effect=xb.BrowserAccessError("verification_required"))
    service = worker(now, collector=collector)
    await service.run_owner("owner", now)
    await service.run_owner("owner", due(index=1))
    assert collector.await_count == 1
    assert xr.state_for("owner")["blocked_reason"] == "verification_required"
    assert not service.send.await_count


@pytest.mark.asyncio
async def test_pause_during_scan_prevents_delivery(runtime):
    now = due()

    async def collect(*args):
        runtime["owner"]["x_research_enabled"] = False
        return [post(now)]

    service = worker(now, collector=collect)
    await service.run_owner("owner", now)
    service.send.assert_not_awaited()
    assert xr.state_for("owner")["status"] == "paused"


@pytest.mark.asyncio
async def test_weak_news_match_is_retained_as_unverified_not_sent(runtime):
    now = due()
    service = worker(now, news_fetcher=AsyncMock(return_value=news(now)[:1]))
    await service.run_owner("owner", now)
    service.send.assert_not_awaited()
    assert xr.fresh_findings("owner", now)[0]["confidence"] == 0.25


@pytest.mark.asyncio
async def test_ambiguous_delivery_is_counted_not_replayed(runtime):
    now = due()
    service = worker(now)
    service.send.side_effect = TimeoutError
    await service.run_owner("owner", now)
    await service.run_owner("owner", now)
    assert service.send.await_count == 1
    assert xr.state_for("owner")["last_delivery"] == "uncertain"
    assert xr.state_for("owner")["share_count"] == 1


@pytest.mark.asyncio
async def test_daily_digest_cap_and_private_identity(runtime):
    now = due()
    service = worker(now)
    state = {
        "share_date": now.astimezone(xr._timezone(PROFILE)).date().isoformat(),
        "share_count": 2,
    }
    await service._deliver(
        "owner", [{"topic": "news", "sources": []}], state, PROFILE, now
    )
    service.send.assert_not_awaited()


def test_context_does_not_force_news_into_unrelated_conversations(runtime):
    assert xr.research_context("owner", "turn off the lights") is None
    assert xr.research_context("owner", "I’m tired today") is None
    assert xr.controls("owner", "hello") is None
    assert "Paused" in xr.controls("owner", "/xresearch pause")
    assert not runtime["owner"]["x_research_enabled"]


@pytest.mark.asyncio
async def test_wrong_browser_account_is_never_used():
    profile = SimpleNamespace(count=AsyncMock(return_value=1))
    profile.first = SimpleNamespace(
        get_attribute=AsyncMock(return_value="/SomeoneElse")
    )
    page = SimpleNamespace(
        url="https://x.com/home",
        get_by_role=lambda role: SimpleNamespace(
            get_by_text=lambda pattern: SimpleNamespace(count=AsyncMock(return_value=0))
        ),
        locator=lambda selector: (
            profile
            if "AppTabBar" in selector
            else SimpleNamespace(count=AsyncMock(return_value=0))
        ),
    )
    with pytest.raises(xb.BrowserAccessError, match="wrong_account"):
        await xb.verify_account(page, "CurieTest")


@pytest.mark.asyncio
async def test_native_scheduler_registers_owner_scoped_job(runtime, monkeypatch):
    upsert = []
    monkeypatch.setattr(xr.task_engine, "use_native", lambda: True)
    monkeypatch.setattr(
        xr.task_engine, "upsert_scheduled", lambda doc: upsert.append(doc)
    )
    monkeypatch.setattr(xr.task_engine, "claim_scheduled", lambda **kw: [])
    await worker(due()).run_once()
    assert len(upsert) == 1 and upsert[0]["owner_id"] == "owner"
    assert upsert[0]["kind"] == xr.KIND


@pytest.mark.asyncio
async def test_same_story_in_different_posts_is_not_shared_twice(runtime):
    now = due()
    service = worker(now)
    await service.run_owner("owner", now)
    service.collector.return_value = [
        post(
            due(index=1),
            9,
            "Hong Kong: major storm closes schools; winds intensify across the city",
        )
    ]
    await service.run_owner("owner", due(index=1))
    assert service.collector.await_count == 2
    assert service.send.await_count == 1
    assert len(xr.fresh_findings("owner", due(index=1))) == 1


@pytest.mark.asyncio
async def test_browser_failure_never_falls_back_to_paid_api(runtime, monkeypatch):
    from connectors import twitter

    paid = AsyncMock(side_effect=AssertionError("Paid X API must not be used"))
    monkeypatch.setattr(twitter, "_request", paid)
    service = worker(
        due(),
        collector=AsyncMock(
            side_effect=xb.BrowserAccessError("browser_unavailable_or_timeout")
        ),
    )
    await service.run_owner("owner", due())
    paid.assert_not_awaited()
    assert xr.state_for("owner")["status"] == "failed"
    assert not xr.state_for("owner").get("blocked_reason")


@pytest.mark.asyncio
async def test_actual_rust_lease_consumes_job_once(runtime, monkeypatch):
    if not xr.task_engine.use_native():
        pytest.skip("Native extension is verified in the Rust/server test job")
    monkeypatch.setattr(
        "memory.repositories.get_repositories",
        lambda: SimpleNamespace(
            profiles=SimpleNamespace(list_with_identities=lambda: [])
        ),
    )
    xr.task_engine.upsert_scheduled(
        {
            "id": "x-native-test",
            "owner_id": "owner",
            "kind": xr.KIND,
            "schedule_type": "once",
            "due_at_ms": 0,
            "payload": {},
        }
    )
    service = worker(due())
    service.run_owner = AsyncMock()
    await service.run_once()
    await service.run_once()
    service.run_owner.assert_awaited_once_with("owner")


def test_no_plaintext_browser_state_files_or_api_imports_in_reader():
    import inspect

    source = inspect.getsource(xb)
    assert "storage_state(path=" not in source
    assert "connectors.twitter" not in source
    assert "connect_over_cdp" not in source
    assert "launch_persistent_context" not in source


@pytest.mark.asyncio
async def test_global_snooze_and_disable_are_respected(runtime):
    now = due()
    runtime["owner"]["proactive_snoozed_until"] = (now + timedelta(days=1)).isoformat()
    service = worker(now)
    await service.run_owner("owner", now)
    service.send.assert_not_awaited()
    assert xr.state_for("owner")["last_delivery"] == "suppressed:snoozed"


@pytest.mark.asyncio
async def test_background_web_response_rate_limit_pauses_even_when_document_is_200(
    monkeypatch,
):
    from contextlib import asynccontextmanager

    callbacks = {}
    page = SimpleNamespace(
        on=lambda event, callback: callbacks.update({event: callback})
    )

    @asynccontextmanager
    async def context(*args, **kwargs):
        yield SimpleNamespace(new_page=AsyncMock(return_value=page))

    async def navigate(*args):
        callbacks["response"](
            SimpleNamespace(status=429, url="https://x.com/i/api/graphql/example")
        )

    monkeypatch.setattr(xb, "browser_context", context)
    monkeypatch.setattr(xb, "_navigate", navigate)
    with pytest.raises(xb.BrowserAccessError, match="restricted_or_rate_limited"):
        await xb._collect({"storage_state": {}}, "CurieTest", [])


def test_session_metadata_cannot_expose_cookies_or_cross_owners(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from services.credential_vault import put_credential

    monkeypatch.setenv("CURIE_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CURIE_CREDENTIAL_STORE", str(tmp_path / "vault.enc"))
    put_credential(
        "owner",
        xb.PROVIDER,
        {
            "handle": "CurieTest",
            "saved_at": "today",
            "storage_state": {"cookies": [{"name": "auth", "value": "fixture-only"}]},
        },
    )
    metadata = xb.session_metadata("owner")
    assert metadata["ready"] and "storage_state" not in metadata
    assert "fixture-only" not in str(metadata)
    assert xb.session_metadata("other")["ready"] is False
    assert b"fixture-only" not in (tmp_path / "vault.enc").read_bytes()
