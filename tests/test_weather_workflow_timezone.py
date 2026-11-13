"""Natural weather corrections retain owner-local dates at UTC day boundaries."""

import asyncio
from datetime import datetime, timedelta

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.tooling import get_runtime_registry
from agent.tooling.contracts import CapabilityDefinition
from agent.tooling.weather_tool import WeatherTool
from utils import temporal_context


@pytest.mark.parametrize(
    "followup",
    [
        "Please check the weather in Kyoto for those dates.",
        "Could you check the weather in Kyoto for that trip?",
        "Please check the weather in Kyoto for the trip.",
        "Actually I meant Kyoto. Check that instead.",
    ],
)
@pytest.mark.parametrize(
    "zone,instant",
    [
        ("America/Los_Angeles", "2026-12-31T20:00:00+00:00"),
        ("Asia/Hong_Kong", "2026-12-31T12:00:00+00:00"),
        ("UTC", "2026-12-31T12:00:00+00:00"),
    ],
)
@pytest.mark.parametrize(
    "date_phrase,expected_start,expected_end",
    [
        ("from January 2 through January 4 next year", "2027-01-02", "2027-01-04"),
        ("today", "2026-12-31", "2026-12-31"),
        ("tomorrow", "2027-01-01", "2027-01-01"),
    ],
)
def test_relative_weather_history_keeps_original_dates(
    monkeypatch, followup, zone, instant, date_phrase, expected_start, expected_end
):
    from memory.session_store import get_session_manager

    initial = datetime.fromisoformat(instant)
    current = initial
    real_now = temporal_context.local_now
    real_recent = temporal_context.recent_rows
    monkeypatch.setattr(
        temporal_context,
        "local_now",
        lambda profile=None, now=None: real_now(profile, now or current),
    )
    monkeypatch.setattr(
        temporal_context, "recent_rows", lambda rows: real_recent(rows, now=current)
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": zone},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    manager = get_session_manager()
    history = manager.get_history
    original = f"Please check the rain forecast in Wellington {date_phrase}."
    final_followup = "Please check the weather in Osaka for those dates."
    turn_times = {}

    def dated_history(*args, **kwargs):
        return [
            dict(
                row,
                created_at=(turn_times.get(row.get("content"), current)).isoformat(),
            )
            for row in history(*args, **kwargs)
        ]

    monkeypatch.setattr(manager, "get_history", dated_history)
    requests, calls = [], []

    async def provider(city, start, end, unit="metric"):
        requests.append((city, start.isoformat(), end.isoformat()))
        return {
            "city": city,
            "source": "sandbox",
            "days": [{"low": 10, "high": 18, "precipitation_probability": 60}],
        }

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        assert name == "weather"
        return await WeatherTool().execute(params, context)

    monkeypatch.setattr("utils.weather.get_weather_range", provider)

    async def blocked_current(*args, **kwargs):
        raise AssertionError("This dated forecast must not call current weather")

    monkeypatch.setattr("utils.weather.get_weather", blocked_current)
    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def scenario():
        nonlocal current
        for index, prompt in enumerate((original, followup, final_followup)):
            current = initial + timedelta(hours=(0, 12, 13)[index])
            turn_times[prompt] = current
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "weather-relative-year",
                    "external_user_id": "weather-relative-year",
                    "external_chat_id": "weather-relative-year",
                    "message_id": str(index),
                    "text": prompt,
                }
            )
            assert result["execution_status"] == "completed"
            target = datetime.fromisoformat(expected_start)
            fixed_range = f"from {target.strftime('%B')} {target.day} through {target.strftime('%B')} {target.day} {target.year}"
            expected_query = (
                original.replace("Wellington", "Kyoto")
                .replace("next year", "2027")
                .replace("today", fixed_range)
                .replace("tomorrow", fixed_range)
                if index == 1 and followup.startswith("Actually")
                else prompt
            )
            assert calls[-1] == ("weather", {"query": expected_query})
            if index and date_phrase == "today":
                assert len(requests) == 1
                assert "passed" in result["text"].lower()
                assert ("Kyoto" if index == 1 else "Osaka") in result["text"]
                continue
            assert len(requests) == index + 1
            assert requests[-1] == (
                ("Wellington", "Kyoto", "Osaka")[index],
                expected_start,
                expected_end,
            )

        # An explicit new range must supersede the inherited trip dates,
        # including a previous honest refusal for an already-past date.
        current = initial + timedelta(hours=14)
        replacement = (
            "Please check the weather in Sapporo from January 3 through January 5 2027."
        )
        turn_times[replacement] = current
        before = len(requests)
        result = await workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "weather-relative-year",
                "external_user_id": "weather-relative-year",
                "external_chat_id": "weather-relative-year",
                "message_id": "3",
                "text": replacement,
            }
        )
        assert result["execution_status"] == "completed"
        assert len(calls) == 4
        assert calls[-1] == ("weather", {"query": replacement})
        assert len(requests) == before + 1
        assert requests[-1] == ("Sapporo", "2027-01-03", "2027-01-05")
        assert "passed" not in result["text"].lower()

    asyncio.run(scenario())


@pytest.mark.parametrize("day", ["today", "tomorrow"])
@pytest.mark.parametrize(
    "wording",
    [
        "Could you check whether I need an umbrella in Wellington {day}?",
        "Please check the rain forecast in Wellington {day}.",
    ],
)
@pytest.mark.parametrize(
    "zone,expected_today,expected_tomorrow",
    [
        ("Asia/Hong_Kong", "2027-01-01", "2027-01-02"),
        ("America/Los_Angeles", "2026-12-31", "2027-01-01"),
    ],
)
def test_weather_correction_preserves_owner_local_day(
    monkeypatch, day, zone, expected_today, expected_tomorrow, wording
):
    instant = datetime.fromisoformat("2026-12-31T20:00:00+00:00")
    real_local_now = temporal_context.local_now
    monkeypatch.setattr(
        temporal_context,
        "local_now",
        lambda profile=None, now=None: real_local_now(profile, instant),
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": zone},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    tools, requests = [], []

    async def provider(city, start, end, unit="metric"):
        requests.append((city, start.isoformat(), end.isoformat()))
        return {
            "city": city,
            "source": "sandbox",
            "days": [{"precipitation_probability": 80, "rain_mm": 2}],
        }

    async def execute(name, params, context):
        tools.append((name, dict(params)))
        assert name == "weather"
        return await WeatherTool().execute(params, context)

    monkeypatch.setattr("utils.weather.get_weather_range", provider)
    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def scenario():
        original = wording.format(day=day)
        for index, prompt in enumerate(
            (original, "Actually I meant Kyoto. Check that instead.")
        ):
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "weather-zone",
                    "external_user_id": "weather-zone",
                    "external_chat_id": "weather-zone",
                    "message_id": str(index),
                    "text": prompt,
                }
            )
            assert result["execution_status"] == "completed"
            assert len(tools) == index + 1
            assert tools[-1] == (
                "weather",
                {
                    "query": (
                        original.replace("Wellington", "Kyoto") if index else original
                    )
                },
            )
            expected = expected_today if day == "today" else expected_tomorrow
            assert requests[-1] == (
                "Kyoto" if index else "Wellington",
                expected,
                expected,
            )
            assert day in result["text"].casefold()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "zone,date_phrase",
    [
        (zone, phrase)
        for zone in ["Asia/Hong_Kong", "America/Los_Angeles"]
        for phrase in [
            "January 2 through January 4 2027",
            "2 January to 4 January 2027",
        ]
    ]
    + [("Asia/Hong_Kong", "January 2 through January 4")],
)
@pytest.mark.parametrize(
    "followup",
    [
        "Actually I meant Kyoto. Check that instead.",
        "Could you check the weather in Kyoto for those dates?",
        "Please check the weather in Kyoto for the trip.",
        "Could you check the weather in Kyoto for that trip?",
        "Please check the weather in Kyoto for those dates.",
    ],
)
@pytest.mark.parametrize("advance_hours", [0, 12])
def test_explicit_weather_range_survives_city_correction(
    monkeypatch, zone, date_phrase, followup, advance_hours
):
    instant = datetime.fromisoformat("2026-12-31T20:00:00+00:00")
    real_local_now = temporal_context.local_now
    monkeypatch.setattr(
        temporal_context,
        "local_now",
        lambda profile=None, now=None: real_local_now(profile, instant),
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile",
        lambda *_: {"timezone": zone},
    )
    monkeypatch.setattr(
        CapabilityDefinition, "availability", lambda _: (True, "sandbox")
    )
    calls, requests = [], []

    async def provider(city, start, end, unit="metric"):
        requests.append((city, start.isoformat(), end.isoformat()))
        return {
            "city": city,
            "source": "sandbox",
            "days": [{"low": 10, "high": 18, "precipitation_probability": 60}],
        }

    async def execute(name, params, context):
        calls.append((name, dict(params)))
        assert name == "weather"
        return await WeatherTool().execute(params, context)

    monkeypatch.setattr("utils.weather.get_weather_range", provider)
    monkeypatch.setattr(get_runtime_registry(), "execute", execute)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *args, **kwargs: None

    async def scenario():
        original = f"Could you check the weather in Wellington from {date_phrase}?"
        for index, prompt in enumerate([original, followup]):
            if index:
                # Cross Los Angeles midnight/year-end between conversation turns.
                # Explicit dates must retain their year rather than being rebased.
                monkeypatch.setattr(
                    temporal_context,
                    "local_now",
                    lambda profile=None, now=None: real_local_now(
                        profile, instant + timedelta(hours=advance_hours)
                    ),
                )
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "weather-explicit-range",
                    "external_user_id": "weather-explicit-range",
                    "external_chat_id": "weather-explicit-range",
                    "message_id": str(index),
                    "text": prompt,
                }
            )
            assert result["execution_status"] == "completed"
            assert len(calls) == index + 1
            assert calls[-1] == (
                "weather",
                {
                    "query": (
                        (
                            followup
                            if any(
                                reference in followup
                                for reference in (
                                    "those dates",
                                    "the trip",
                                    "that trip",
                                )
                            )
                            else original.replace("Wellington", "Kyoto")
                        )
                        if index
                        else original
                    )
                },
            )
            assert requests[-1] == (
                "Kyoto" if index else "Wellington",
                "2027-01-02",
                "2027-01-04",
            )
            assert "Jan 2 to Jan 4" in result["text"]

    asyncio.run(scenario())
