from datetime import timedelta
import pytest
from services import ai_scout as scout
from services.scout_discovery import devin_updates
from tests.test_ai_scout import NOW, release

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
