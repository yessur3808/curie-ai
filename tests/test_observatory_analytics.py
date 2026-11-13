import asyncio
import json

import pytest

from agent.intent_router import classify_request
from agent.personality_context import PersonalityContext
from agent.tooling.contracts import ToolContext
from agent.tooling.observatory_tool import ObservatoryProjectAnalyticsTool
from contracts import model_capability_prompt
from services.observatory_analytics import (
    observatory_analytics_available,
    read_project_analytics,
)
from utils.formatting import telegram_html

pytestmark = pytest.mark.security


def _write_observatory_fixture(root):
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "portfolio",
                        "name": "Personal website",
                        "url": "https://example.com",
                        "gaProperty": "111",
                    },
                    {
                        "id": "atmosphere",
                        "name": "Atmosphere",
                        "url": "https://youratmosphere.app",
                        "gaProperty": "222",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "analytics-cache.json").write_text(
        json.dumps(
            {
                "222:30": {
                    "attempt": "2026-10-30T12:00:01Z",
                    "report": {
                        "status": "live",
                        "source": "GA4",
                        "property": "222",
                        "days": 30,
                        "range": {
                            "start": "2026-09-30",
                            "end": "2026-10-29",
                            "previousStart": "2026-08-31",
                            "previousEnd": "2026-09-29",
                        },
                        "totals": {
                            "screenPageViews": 0,
                            "activeUsers": 0,
                            "newUsers": 0,
                            "sessions": 0,
                            "eventCount": 0,
                        },
                        "previous": {
                            "screenPageViews": 16,
                            "activeUsers": 4,
                            "newUsers": 4,
                            "sessions": 33,
                        },
                        "breakdowns": {"pages": {"rows": [{"path": "/private"}]}},
                        "updatedAt": "2026-10-30T12:00:00Z",
                        "timeZone": "Asia/Hong_Kong",
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_reads_only_sanitized_aggregate_observatory_analytics(tmp_path):
    root = tmp_path / "observatory"
    _write_observatory_fixture(root)

    report = read_project_analytics(
        "Did my Atmosphere website get any views lately?",
        data_dir=root,
    )

    assert report["status"] == "live"
    assert report["project"] == {"id": "atmosphere", "name": "Atmosphere"}
    assert report["totals"]["screenPageViews"] == 0
    assert report["previous"]["screenPageViews"] == 16
    assert "property" not in report
    assert "breakdowns" not in report


def test_tool_reports_zero_as_measured_data_and_includes_freshness(
    tmp_path, monkeypatch
):
    root = tmp_path / "observatory"
    _write_observatory_fixture(root)
    monkeypatch.setenv("CURIE_OBSERVATORY_DATA_DIR", str(root))

    result = asyncio.run(
        ObservatoryProjectAnalyticsTool().execute(
            {"query": "Atmosphere website views", "days": 30},
            ToolContext("owner", platform="telegram"),
        )
    )

    assert "**Atmosphere — Analytics overview**" in result.text
    assert "**Page views:** 0 · ↓ 100% vs previous period" in result.text
    assert "**Comparison period**" in result.text and "16 views" in result.text
    assert "**Assessment**" in result.text
    assert "Updated 30 Oct 2026, 20:00 HKT" in result.text
    assert "2026-10-30T12:00:00Z" not in result.text
    rendered = telegram_html(result.text)
    assert "<b>Atmosphere — Analytics overview</b>" in rendered
    assert "<b>Page views:</b> 0" in rendered
    assert result.source == "Curie Observatory local analytics cache / GA4"


def test_tool_requires_a_project_when_request_is_ambiguous(tmp_path, monkeypatch):
    root = tmp_path / "observatory"
    _write_observatory_fixture(root)
    monkeypatch.setenv("CURIE_OBSERVATORY_DATA_DIR", str(root))

    result = asyncio.run(
        ObservatoryProjectAnalyticsTool().execute(
            {"query": "Show website analytics", "days": 30},
            ToolContext("owner", platform="telegram"),
        )
    )

    assert result.data["status"] == "project_required"
    assert "Personal website" in result.text and "Atmosphere" in result.text


def test_observatory_availability_probe_checks_only_bounded_files(
    tmp_path, monkeypatch
):
    root = tmp_path / "observatory"
    monkeypatch.setenv("CURIE_OBSERVATORY_DATA_DIR", str(root))
    assert observatory_analytics_available()[0] is False
    _write_observatory_fixture(root)
    assert observatory_analytics_available() == (True, None)


def test_natural_analytics_request_routes_separately_from_operations_dashboard():
    analytics = classify_request("Did my atmosphere website get any views lately?")
    dashboard = classify_request("Show me the Curie dashboard status")
    combined = classify_request("Check the dashboard for Atmosphere views")
    opinion = classify_request("What are your views on this project?")

    assert analytics.action == "observatory_project_analytics"
    assert analytics.params["days"] == 30
    assert combined.action == "observatory_project_analytics"
    assert dashboard.action == "dashboard_snapshot"
    assert opinion is None


def test_runtime_prompt_retrieves_relevant_tool_descriptions_not_only_names(
    tmp_path, monkeypatch
):
    root = tmp_path / "observatory"
    _write_observatory_fixture(root)
    monkeypatch.setenv("CURIE_OBSERVATORY_DATA_DIR", str(root))

    manifest = model_capability_prompt(query="How many views did my website get?")
    directives = "\n".join(PersonalityContext({}).build_prompt_directives("Hello"))

    assert "- observatory_project_analytics [available; chat_tool" in manifest
    assert "Read aggregate GA4 page views" in manifest
    assert (
        "use observatory_project_analytics for website/project analytics" in directives
    )
