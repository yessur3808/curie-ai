from datetime import timedelta

import pytest

from services import ai_scout as scout
from services.scout_novelty import novel_engineering, same_content, substantive_text
from tests.test_ai_scout import NOW, release, worker
from tests import test_ai_scout as scout_fixtures

runtime = scout_fixtures.runtime

PREFS = {"focus": "engineering_lead", "discovery_scope": "hands_on_agent_tools"}
CHANGE = "Adds persistent memory for coding agents with explicit owner permissions and reproducible regression workflows."


def finding(version, excerpt=CHANGE, **kw):
    return release(version, title=f"Hermes agent {version}", excerpt=excerpt, **kw)


@pytest.mark.parametrize(
    "item",
    [
        finding(
            "canary.20261114",
            "Downloads for the coding agent with native test smoke checks across macOS and Windows architectures.",
        ),
        finding("abandoned-rc.6", '{"attempt":6,"schema":1,"version":"0.23.2"}'),
        finding(
            "v2",
            "What's Changed test(memory): update test fixtures and regression assertions by @ci[bot] in #12 chore: bump version to 2.0.1",
        ),
        finding(
            "stable",
            "Hermes Agent stable release with desktop installers and signed packages for all supported systems. Downloads OS Architecture macOS Windows Smoke status Passed",
        ),
    ],
)
def test_release_plumbing_does_not_become_engineering_news(item):
    assert scout.rank_candidates([item], {}, PREFS, NOW) == []


def test_new_version_and_url_do_not_make_unchanged_content_new():
    old, new = finding("1.2.3"), finding("1.2.4")
    assert not scout.rank_candidates([new], {}, PREFS, NOW, history=[old])
    assert len(scout.rank_candidates([old, new], {}, PREFS, NOW)) == 1


def test_real_new_change_and_changed_quantities_remain_eligible():
    old = finding("1.2.3")
    new = finding(
        "1.2.4",
        "Fixes coding agent credential leakage by blocking cross-owner API access and rotating exposed authentication tokens.",
    )
    assert (
        scout.rank_candidates([new], {}, PREFS, NOW, history=[old])[0]["id"] == "1.2.4"
    )
    assert not same_content(
        finding("one", CHANGE + " Latency is 10 ms."),
        finding("two", CHANGE + " Latency is 20 ms."),
    )
    assert not same_content(
        finding("one", CHANGE + " Allows writes but not reads."),
        finding("two", CHANGE + " Allows reads but not writes."),
    )


def test_cleanup_preserves_actual_fix_and_removes_bot_and_test_noise():
    item = finding(
        "v2",
        "What's Changed test(startup): isolate fixtures by @ci[bot] in #1 fix(memory): prevent cross-owner reads for coding agents by @dev[bot] in #2 docs: update examples Full Changelog: v1...v2 Downloads macOS Windows",
    )
    text = substantive_text(item)
    assert "prevent cross-owner reads" in text
    for noise in ("[bot]", "isolate fixtures", "update examples", "Downloads", "#2"):
        assert noise not in text


def test_bad_timestamp_cannot_hide_a_valid_candidate():
    invalid = finding("invalid", published_at=(NOW + timedelta(days=1)).isoformat())
    valid = finding("valid")
    assert scout.rank_candidates([invalid, valid], {}, PREFS, NOW)[0]["id"] == "valid"


def test_publisher_diversity_is_not_a_blanket_topic_ban():
    a = finding("a")
    b = finding(
        "b",
        "Adds remote coding agent session handoff with scoped API access and durable reconnection recovery.",
    )
    c = finding(
        "c",
        "Adds tracing for coding agent debugging with reproducible execution logs and failing task replay.",
        publisher="another/tool",
    )
    assert [x["id"] for x in novel_engineering([a, b, c], one_per_publisher=True)] == [
        "a",
        "c",
    ]


@pytest.mark.asyncio
async def test_delivery_checks_persisted_content_even_if_ranking_was_bypassed(runtime):
    scout.set_focus("owner", "engineering_lead")
    old, new = finding("old"), finding("new")
    scout.save_personal_item("owner", scout.FINDING, old)
    state = {"coverage": {"x": "read"}, "reported_urls": [old["url"]]}
    service = worker()
    await service.deliver("owner", [new], state, "novelty-delivery", NOW)
    service.send.assert_not_awaited()
    assert state["delivery"] == "no_relevant_items"
    # Another owner must not inherit this owner's delivery history.
    assert scout.rank_candidates(
        [new], {}, PREFS, NOW, history=scout.list_personal_items("other", scout.FINDING)
    )


@pytest.mark.asyncio
async def test_noisy_releases_produce_no_review_and_no_message(runtime):
    scout.set_focus("owner", "engineering_lead")
    service = worker(items=[finding("canary.20261114")])
    await service.run_owner("owner", NOW)
    service.reviewer.assert_not_awaited()
    service.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_similar_new_url_after_uncertain_send_is_not_retried(runtime):
    from unittest.mock import AsyncMock

    scout.set_focus("owner", "engineering_lead")
    service = worker(
        fetcher=AsyncMock(side_effect=[([finding("one")], []), ([finding("two")], [])]),
        send=AsyncMock(return_value=False),
    )
    await service.run_owner("owner", NOW)
    await service.run_owner("owner", NOW + timedelta(hours=5))
    assert service.send.await_count == 1
    assert service.reviewer.await_count == 1
    assert scout.state_for("owner")["delivery"] == "uncertain"
