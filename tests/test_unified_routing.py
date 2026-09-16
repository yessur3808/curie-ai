import asyncio

import pytest

from agent.routing import RoutingDecision, record_routing_correction, route_request
from memory import local_store

pytestmark = pytest.mark.security


def _route(text: str, owner: str = "u1") -> RoutingDecision:
    return asyncio.run(route_request(text, owner))


@pytest.mark.parametrize(
    ("text", "intent", "capability", "live", "risk"),
    [
        ("Hello Curie", "social", None, False, "none"),
        ("Is it raining in Paris?", "capability", "weather", True, "read_only"),
        (
            "Fix the authentication bug in this project",
            "capability",
            "project_change",
            False,
            "mutating",
        ),
        ("Remind me tomorrow", "capability", "scheduler_skill", False, "mutating"),
        ("Tell me a joke", "conversation", None, False, "none"),
    ],
)
def test_every_request_has_one_typed_explainable_decision(
    text, intent, capability, live, risk
):
    decision = _route(text)
    assert decision.intent == intent
    assert decision.selected_capability == capability
    assert decision.live_data_required is live
    assert decision.risk == risk
    assert decision.explanation
    assert 0 <= decision.confidence <= 1


def test_multiple_independent_intents_get_one_ordering_question():
    decision = _route("Check the weather in Paris and then run the project tests")
    assert decision.intent == "multiple_intents"
    assert set(decision.alternatives) == {"weather", "run_tests"}
    assert "which" in decision.parameters["message"].casefold()


def test_route_resolves_home_control_pronoun_from_recent_history():
    decision = asyncio.run(
        route_request(
            "Switch it off",
            "u1",
            history=[("user", "Is the TV light on?")],
        )
    )

    assert decision.intent == "capability"
    assert decision.selected_capability == "home_control"
    assert decision.parameters["target"] == "TV light"


def test_route_keeps_multiple_devices_for_plural_follow_up():
    decision = asyncio.run(
        route_request(
            "Please turn them on",
            "u1",
            history=[
                ("user", "Turn on the floor lamp and tv light"),
                ("assistant", "Which devices?"),
            ],
        )
    )

    assert decision.intent == "capability"
    assert decision.selected_capability == "home_control"
    assert decision.parameters["targets"] == ["floor lamp", "tv light"]


def test_route_retries_recent_failed_home_command_without_model_inference():
    decision = asyncio.run(
        route_request(
            "Try again",
            "u1",
            history=[
                ("user", "Turn on the DreamView"),
                ("assistant", "Not yet. The command didn't take effect."),
            ],
        )
    )

    assert decision.intent == "capability"
    assert decision.selected_capability == "home_control"
    assert decision.parameters["target"] == "DreamView"


def test_model_cannot_route_mutating_capability(monkeypatch):
    async def fake_model(*args, **kwargs):
        return (
            '{"action":"project_change","params":{"request":"edit"},"confidence":0.99}'
        )

    monkeypatch.setattr("agent.intent_router._ask_intent_model", fake_model)
    decision = _route("Could you investigate and maybe alter something obscure?")
    assert decision.intent == "conversation"
    assert decision.selected_capability is None


def test_low_confidence_model_falls_back_to_conversation(monkeypatch):
    async def fake_model(*args, **kwargs):
        return '{"action":"research","params":{"query":"chips"},"confidence":0.2}'

    monkeypatch.setattr("agent.intent_router._ask_intent_model", fake_model)
    assert _route("Could you investigate chips somehow?").intent == "conversation"


def test_simple_thanks_uses_bounded_social_route():
    decision = _route("Thanks")
    assert decision.intent == "social"


def test_correction_with_tool_noun_uses_safe_social_route(monkeypatch):
    async def fake_model(*args, **kwargs):
        return (
            '{"action":"inspect_project","params":{},"confidence":0.5,'
            '"clarification":"What project do you mean?"}'
        )

    monkeypatch.setattr("agent.intent_router._ask_intent_model", fake_model)
    decision = _route("There is no project")
    assert decision.intent == "social"
    assert decision.selected_capability is None


def test_unrelated_specialist_cannot_intercept_conversation():
    decision = _route("My trip to Paris was fun, and the weather metaphor was clever")
    assert decision.intent == "conversation"
    assert decision.selected_capability is None


def test_routing_outcomes_are_anonymized_and_corrections_owner_scoped(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    decision = _route("Is it raining in Paris?", "owner-a")
    rows = local_store.list_routing_outcomes("owner-a")
    assert rows[0]["id"] == decision.id
    assert "Paris" not in str(rows[0])
    assert len(rows[0]["request_hash"]) == 64
    record_routing_correction("owner-a", decision.id, "research")
    assert (
        local_store.list_routing_outcomes("owner-a")[0]["corrected_capability"]
        == "research"
    )
    assert local_store.list_routing_outcomes("owner-b") == []


def test_invalid_decision_schema_is_rejected():
    with pytest.raises(ValueError):
        RoutingDecision(id="x", intent="capability", confidence=1.1)
