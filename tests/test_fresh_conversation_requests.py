import pytest

from agent.intent_router import classify_request


@pytest.mark.parametrize(
    "text,action",
    [
        ("Could you take a look at how Curie is doing?", "dashboard_snapshot"),
        ("How much RAM do you have left?", "ram_usage"),
        ("How much memory is available?", "ram_usage"),
        ("Can you check whether my desk lamp is switched on?", "home_status"),
        ("Could you pull together the latest local AI news?", "research"),
        ("Have I got anything scheduled for tomorrow?", "calendar_list"),
    ],
)
def test_fresh_requests(text, action):
    result = classify_request(text)
    assert result is not None and result.action == action
    if action == "home_status":
        assert result.params["target"] == "desk lamp"


@pytest.mark.parametrize(
    "text",
    [
        "Could you take a look at how Curie is doing in my novel?",
        "Can you check whether the lamp in my story is switched on?",
    ],
)
def test_not_overbroad(text):
    assert classify_request(text) is None


@pytest.mark.parametrize(
    "original",
    [
        "Could you see whether it will rain in Osaka tomorrow?",
        "Check the weather in Osaka today.",
        "Check the weather in Osaka from November 5 to November 7.",
    ],
)
def test_location_correction_preserves_rest_of_request(original):
    result = classify_request(
        "Actually I meant Kyoto. Check that instead.",
        [{"role": "user", "content": original}],
    )
    assert result.action == "weather"
    assert result.params["query"] == original.replace("Osaka", "Kyoto")


def test_ram_available_is_measured_not_total_minus_used(monkeypatch):
    from types import SimpleNamespace
    import psutil
    from agent.tooling.system_tools import _ram_snapshot

    monkeypatch.setattr(
        psutil,
        "virtual_memory",
        lambda: SimpleNamespace(
            percent=50, used=4 * 1024**3, total=8 * 1024**3, available=5 * 1024**3
        ),
    )
    monkeypatch.setattr(psutil, "process_iter", lambda fields: [])
    text, data = _ram_snapshot()
    assert "Host RAM" in text and "Available: 5.0 GiB" in text
    assert data["available"] == 5 * 1024**3


@pytest.mark.parametrize(
    "query,city",
    [
        ("Will it rain in Osaka tomorrow?", "Osaka"),
        ("Weather in Kyoto", "Kyoto"),
        ("Forecast for Ulaanbaatar from November 5 to November 7", "Ulaanbaatar"),
        ("Rain in my heart", None),
    ],
)
def test_weather_place_names_reach_geocoder(query, city):
    from utils.datetime_info import extract_city_from_message

    assert extract_city_from_message(query) == city


def test_model_prompt_exposes_intent_capability_relationship():
    from agent.understanding.classifier import _prompt, _parse, relevant_taxonomy_subset

    subset = relevant_taxonomy_subset(
        "Could you inspect the current RAM pressure on this machine?"
    )
    capabilities = {"ram_usage": "Inspect current memory usage."}
    prompt = _prompt("Inspect RAM pressure", subset, capabilities)
    assert '"candidate_capabilities": ["ram_usage"]' in prompt
    # Keep rejecting inconsistent output; repair the prompt, not the validator.
    assert (
        _parse(
            '{"intent":"current_knowledge","confidence":0.9,"entities":{},"candidate_capability":"ram_usage"}',
            subset,
            capabilities,
        )
        is None
    )
