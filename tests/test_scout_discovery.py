from datetime import timedelta
import pytest
from services import ai_scout as scout
from services.scout_discovery import devin_updates
from tests.test_ai_scout import NOW, release, runtime, worker  # noqa: F401

PREFS = {"focus": "engineering_lead", "discovery_scope": "capability_discoveries"}


@pytest.mark.parametrize(
    "name",
    ["T3 Code", "t3code", "Zeron", "Paseo", "Devin", "Hermes", "MausBot", "OpenBot"],
)
def test_requested_tool_features_are_eligible(name):
    item = release(
        name,
        title=name + " new workspace",
        excerpt="Adds parallel sessions with isolated workspaces so agents can implement separate tasks without conflicting edits.",
    )
    assert scout.rank_candidates([item], {}, PREFS, NOW)


def test_capable_open_model_restored_but_not_generic_headline():
    model = release(
        "model",
        topic="models",
        title="New open-weight LLM",
        age=72,
        excerpt="Adds improved coding and tool calling with a longer context window and published hardware requirements for local deployment.",
    )
    assert scout.rank_candidates([model], {}, PREFS, NOW)
    assert not scout.rank_candidates(
        [model], {}, {"discovery_scope": "hands_on_agent_tools"}, NOW
    )
    model["excerpt"] = (
        "A new model announcement with a launch event and enthusiastic community discussion, without technical details."
    )
    assert not scout.rank_candidates([model], {}, PREFS, NOW)


def test_nightly_feature_not_download_plumbing():
    feature = release(
        "nightly",
        title="T3 Code nightly",
        excerpt="Adds remote session handoff so a developer can continue work across desktop and phone with the original context.",
    )
    assert scout.rank_candidates([feature], {}, PREFS, NOW)
    feature["excerpt"] = (
        "Downloads OS Architecture Smoke status Passed. Packages for every platform, desktop installers and signatures."
    )
    assert not scout.rank_candidates([feature], {}, PREFS, NOW)


def test_devin_official_dates_and_feature_parsing():
    date = (NOW - timedelta(days=3)).strftime("%B %d, %Y")
    body = f"**{date}**\n\n### Sessions\n\n**Remote sessions**\n\nDevin now supports remote sessions with durable context so work can continue after disconnecting the desktop.\n".encode()
    items = devin_updates(body, NOW)
    assert len(items) == 1
    assert items[0]["title"] == "Devin: Remote sessions"
    assert items[0]["url"].startswith("https://docs.devin.ai/release-notes/overview#")
    assert not devin_updates(body, NOW + timedelta(days=8))


def test_atom_model_article_parsed_without_off_domain_links():
    body = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Open model with better coding</title><published>{NOW.isoformat()}</published><link href="https://simonwillison.net/model"/><summary>New open weights improve coding and tool use, with hardware requirements and an example.</summary></entry></feed>'.encode()
    assert scout.parse_blog_feed(
        body, "Simon Willison", "simonwillison.net", "models", NOW
    )
    assert not scout.parse_blog_feed(body, "Other", "example.com", "models", NOW)


def test_exploratory_queries_cover_topics_and_rotate_creators():
    prefs = {
        **PREFS,
        "discovery_style": "exploratory",
        "preferred_accounts": ["theo", "ThePrimeagen", "simonw", "swyx"],
    }
    first = scout.discovery_queries(prefs, 0, NOW)
    second = scout.discovery_queries(prefs, 1, NOW)
    assert len(first) == 3
    assert all("from:" not in query for query in first[:2])
    assert first[2] != second[2]
    assert not any(
        name in " ".join(first + second) for name in ["Devin", "Paseo", "T3 Code"]
    )


def test_unknown_tool_has_same_score_as_named_example():
    excerpt = "New AI workspace adds parallel sessions with isolated workspaces so coding agents can implement independent tasks."
    unknown = release("unknown", title="Unfamiliar AI workspace", excerpt=excerpt)
    familiar = release("familiar", title="T3 Code workspace", excerpt=excerpt)
    familiar["publisher"] = "pingdotgg/t3code"
    first = scout.rank_candidates([unknown], {}, PREFS, NOW)
    second = scout.rank_candidates([familiar], {}, PREFS, NOW)
    assert first[0]["score"] == second[0]["score"]


def test_explanation_is_feature_linked_and_does_not_claim_testing():
    item = release(
        "unknown",
        title="Unfamiliar coding agent",
        excerpt="Adds remote session handoff so a developer can continue work across desktop and phone with the original context.",
    )
    card = scout.report_parts(
        [item], {"x": "not_authenticated"}, {**PREFS, "explain_tools": True}
    )[0]
    assert "Where I’d use it" in card
    assert "away from your desk" in card
    assert "potential benefit" in card
    assert "Not independently tested" in card
    assert "X browsing is unavailable" in card
    assert len(card) < 4096


@pytest.mark.asyncio
async def test_project_cache_daily_but_editorial_feed_stays_fresh(monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(304)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        scout.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(scout, "FEEDS", (("example/tool", "tools"),))
    monkeypatch.setattr(
        scout,
        "BLOGS",
        (("Editorial", "https://example.org/feed", "example.org", "tools"),),
    )
    cached_item = release("cached")
    cache = {
        name: {"checked_at": NOW.isoformat(), "etag": "test", "items": [cached_item]}
        for name in ["example/tool", "Devin", "Editorial"]
    }
    items, errors = await scout.fetch_releases(NOW + timedelta(hours=4), cache)
    assert not errors
    assert calls == ["https://example.org/feed"]
    assert cached_item in items
    calls.clear()
    await scout.fetch_releases(NOW + timedelta(hours=25), cache)
    assert len(calls) == 3
    assert (
        cache["example/tool"]["checked_at"] == (NOW + timedelta(hours=25)).isoformat()
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("runtime")
async def test_capability_survives_delivery_and_seven_day_expiry():
    from memory.local_store import save_personal_item

    save_personal_item(
        "owner", scout.PREFERENCES, {"id": "ai-scout-preferences-v1", **PREFS}
    )
    item = release(
        "new",
        age=72,
        title="ADE orchestration",
        excerpt="Adds isolated workspaces and parallel sessions so independent tasks can progress together with separate changes.",
    )
    assert scout.engineering_score(item) < 3
    service = worker(items=[item])
    await service.run_owner("owner", NOW)
    service.send.assert_awaited_once()
    assert scout.latest("owner", NOW)
    assert not scout.latest("owner", NOW + timedelta(days=5))
