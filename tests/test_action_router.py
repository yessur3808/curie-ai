import asyncio
from pathlib import Path

import pytest

import agent.action_router as router
from agent.tooling import policies
import memory.local_store as local_store

pytestmark = pytest.mark.security


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    monkeypatch.setattr(policies, "WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(policies, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "workspace").mkdir()
    monkeypatch.delenv("MASTER_USER_ID", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("text", "action"),
    [
        (
            "Create a new Python project called weather-dashboard.",
            "create_python_project",
        ),
        ("Look through this project and show me its files", "inspect_project"),
        ("Look through this project and fix the authentication bug.", "project_change"),
        ("Generate the API endpoint and run its tests.", "project_change"),
        ("What is using all my RAM?", "ram_usage"),
        ("Measure the current network speed and latency", "network_speed"),
        ("Check tomorrow's weather and tell me whether I need my jacket.", "weather"),
        ("Is it raining now in hong kong?", "weather"),
        ("Research a seven-day trip to Paris within my budget.", "research"),
        ("Analyze semiconductor market trends using current sources.", "research"),
    ],
)
def test_natural_requests_are_classified(text, action):
    assert router.classify_request(text).action == action


def test_combined_code_request_preserves_test_intent():
    request = router.classify_request("Generate the API endpoint and run its tests.")
    assert request.params["run_tests"] is True


def test_path_escape_is_blocked(isolated):
    with pytest.raises(PermissionError):
        policies.safe_path("../../outside")


def test_project_is_created_in_per_user_sandbox(isolated, monkeypatch):
    monkeypatch.setattr(policies, "remember_active_project", lambda *a, **k: None)
    req = router.classify_request(
        "Create a new Python project called weather-dashboard"
    )
    result = asyncio.run(router.execute_request(req, "user-1", {}))
    project = isolated / "projects" / "user-1" / "weather-dashboard"
    assert "Created Python project" in result
    assert (project / "pyproject.toml").exists()
    assert (project / "tests" / "test_smoke.py").exists()


def test_generated_change_requires_user_bound_approval(isolated):
    req = router.classify_request("Fix the authentication bug in this project")
    response = asyncio.run(router.execute_request(req, "user-1", {}))
    token = response.split("/approve action ", 1)[1][:8]
    wrong = asyncio.run(
        router.execute_request(
            router.ToolRequest("approve", {"token": token}), "user-2", {}
        )
    )
    assert "invalid" in wrong.lower()
    rejected = asyncio.run(
        router.execute_request(
            router.ToolRequest("reject", {"token": token}), "user-1", {}
        )
    )
    assert "no changes" in rejected.lower()


def test_command_executor_has_allowlist_and_no_shell(isolated):
    with pytest.raises(PermissionError):
        policies.run_sandboxed(["bash", "-c", "touch bad"], isolated)


def test_audit_records_completed_action(isolated):
    result = asyncio.run(
        router.execute_request(router.ToolRequest("inspect_project"), "u", {})
    )
    assert "Files under" in result
    audit = local_store.list_action_audit("u")
    assert audit[0]["action"] == "inspect_project"
    assert audit[0]["status"] == "completed"
    details = audit[0]["details"]
    assert details["validated_action"] == "inspect_project"
    assert details["policy_decision"] == "allowed"
    assert details["tool_version"] != "unknown"


def test_invalid_approval_is_a_redacted_security_event(isolated):
    result = asyncio.run(
        router.execute_request(
            router.ToolRequest("approve", {"token": "deadbeef"}),
            "u",
            {"_connector": "api"},
        )
    )
    assert "invalid" in result.lower()
    details = local_store.list_action_audit("u")[0]["details"]
    assert details["security_category"] == "approval_failure"
    assert details["approval"]["token"] == "[REDACTED]"


def test_weather_uses_profile_location_and_preferences(isolated, monkeypatch):
    async def fake_weather(city, unit="metric", day_offset=0):
        assert city == "Paris"
        assert day_offset == 1
        return {
            "city": city,
            "temperature": 9,
            "description": "Rain",
            "tips": ["Take a jacket."],
        }

    monkeypatch.setattr("utils.weather.get_weather", fake_weather)
    req = router.classify_request(
        "Check tomorrow's weather and tell me if I need my jacket"
    )
    result = asyncio.run(
        router.execute_request(
            req, "u", {"location": "Paris", "jacket_preference": "blue wool coat"}
        )
    )
    assert "Take a jacket" in result
    assert "blue wool coat" in result


def test_raining_now_extracts_hong_kong_and_uses_live_weather(isolated, monkeypatch):
    async def fake_weather(city, unit="metric", day_offset=0):
        assert city.lower() == "hong kong"
        assert day_offset == 0
        return {
            "city": "Hong Kong",
            "temperature": 27,
            "description": "Light rain",
            "is_raining": True,
            "tips": ["Bring an umbrella."],
        }

    monkeypatch.setattr("utils.weather.get_weather", fake_weather)
    request = router.classify_request("Is it raining now in hong kong?")
    result = asyncio.run(router.execute_request(request, "u", {}))
    assert result.startswith("Yes.")
    assert "light rain" in result
    assert "27°C" in result
    assert "umbrella" in result


def test_named_trip_destination_overrides_profile_location(isolated, monkeypatch):
    observed = []

    async def fake_weather(city, unit="metric", day_offset=0):
        observed.append(city)
        return {
            "city": city,
            "temperature": 22,
            "description": "Clear sky",
            "tips": [],
        }

    monkeypatch.setattr("utils.weather.get_weather", fake_weather)
    request = router.classify_request("What is the weather in Los Angeles today?")
    result = asyncio.run(
        router.execute_request(request, "u", {"location": "Hong Kong"})
    )
    assert observed == ["Los Angeles"]
    assert "Los Angeles" in result
    assert "Hong Kong" not in result


def test_weather_followup_will_i_need_jacket_is_routed():
    request = router.classify_request("Will I need a jacket for those dates?")
    assert request is not None
    assert request.action == "weather"


def test_humidity_question_is_routed_and_uses_live_value(isolated, monkeypatch):
    async def fake_weather(city, unit="metric", day_offset=0):
        assert city == "Los Angeles"
        return {
            "city": city,
            "temperature": 22,
            "relative_humidity": 61,
            "description": "Partly cloudy",
            "tips": [],
            "source": "Open-Meteo",
        }

    monkeypatch.setattr("utils.weather.get_weather", fake_weather)
    request = router.classify_request("What is the humidity in Los Angeles?")
    assert request is not None and request.action == "weather"
    result = asyncio.run(router.execute_request(request, "u", {}))
    assert "61%" in result
    assert "Los Angeles" in result


def test_distant_trip_range_is_not_replaced_with_current_conditions(
    isolated, monkeypatch
):
    from datetime import date, timedelta

    async def should_not_fetch_current(*_args, **_kwargs):
        raise AssertionError("current weather must not answer a future date range")

    future = date.today() + timedelta(days=30)
    monkeypatch.setattr("utils.weather.get_weather", should_not_fetch_current)
    monkeypatch.setattr(
        "agent.tooling.weather_tool._requested_range",
        lambda _query: (future, future + timedelta(days=4)),
    )
    request = router.classify_request(
        "What will the weather in Los Angeles be from October 1 to October 5?"
    )
    result = asyncio.run(
        router.execute_request(request, "u", {"location": "Hong Kong"})
    )
    assert "not available yet" in result
    assert "Los Angeles" in result
    assert "Hong Kong" not in result


def test_weather_date_range_parser_supports_transcript_wording():
    from datetime import date
    from agent.tooling.weather_tool import _requested_range

    assert _requested_range(
        "weather for my Los Angeles trip from October 1 to October 9?",
        today=date(2026, 9, 12),
    ) == (date(2026, 10, 1), date(2026, 10, 9))


def test_missing_command_exit_status_is_not_falsely_audited_as_success_code(
    isolated, monkeypatch
):
    from agent.tooling.contracts import ToolResult

    class Registry:
        def get(self, _name):
            return type(
                "Definition",
                (),
                {"audit_redactions": (), "version": "1", "approval_policy": "never"},
            )()

        async def execute(self, _name, _params, _context):
            return ToolResult("done", {"command": ["pytest", "-q"]})

    monkeypatch.setattr("agent.tooling.get_runtime_registry", lambda: Registry())
    asyncio.run(router.execute_request(router.ToolRequest("run_tests"), "u", {}))
    details = local_store.list_action_audit("u")[0]["details"]
    assert details["command_exit_status"] is None


def test_mutating_tool_outcome_records_destination_verification(isolated, monkeypatch):
    from agent.tooling.contracts import ToolResult

    class Registry:
        def get(self, _name):
            return type(
                "Definition",
                (),
                {
                    "audit_redactions": (),
                    "version": "1",
                    "approval_policy": "never",
                    "risk": "mutating",
                },
            )()

        async def execute(self, _name, _params, _context):
            return ToolResult(
                "Done. DreamView is off.",
                {
                    "receipt": {
                        "requested_state": "off",
                        "verified_state": "off",
                    }
                },
            )

    monkeypatch.setattr("agent.tooling.get_runtime_registry", lambda: Registry())
    outcome = {}
    result = asyncio.run(
        router.execute_request(
            router.ToolRequest("home_control", {"target": "DreamView", "state": "off"}),
            "u",
            {},
            _outcome=outcome,
        )
    )

    assert result == "Done. DreamView is off."
    assert outcome["status"] == "verified"
    assert outcome["verification_status"] == "verified"
    assert (
        local_store.list_action_audit("u")[0]["details"]["verification_status"]
        == "verified"
    )


def test_live_research_preserves_sources(isolated, monkeypatch):
    import importlib

    async def fake_find_info(query, *, return_metadata=False):
        text = "Current summary\n\nSources:\n- https://example.com/report"
        if return_metadata:
            return {
                "answer": text,
                "sources": [
                    {
                        "id": "S1",
                        "url": "https://example.com/report",
                        "passage": "Current summary",
                        "fetched_at": "2026-08-23T00:00:00+00:00",
                        "freshness": "live_fetch",
                    }
                ],
            }
        return text

    find_info_module = importlib.import_module("agent.skills.find_info")
    monkeypatch.setattr(find_info_module, "find_info", fake_find_info)
    req = router.classify_request(
        "Analyze semiconductor market trends using current sources"
    )
    result = asyncio.run(router.execute_request(req, "u", {}))
    assert "https://example.com/report" in result
