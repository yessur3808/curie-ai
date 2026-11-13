"""Natural language permission boundaries must precede tool selection."""

import asyncio

import pytest

from agent.intent_router import classify_request, resolve_request
from agent.routing import route_request, route_operational_request


@pytest.mark.parametrize(
    "text",
    [
        "Don't check the weather in Paris.",
        "Please do not read my calendar tomorrow.",
        "Don’t turn off the desk lamp.",
        "Don't turn off the desk lamp, just check its status.",
        "Explain how you would check my calendar tomorrow without accessing it.",
        "Could you explain how to check my calendar tomorrow?",
        "Can you explain how you'd check my calendar?",
        "Could you describe how you’d search my inbox?",
        "Show me how you would search my inbox.",
        "Check the weather in Paris without actually calling tools.",
    ],
)
def test_discussion_and_prohibitions_do_not_select_tools(text, monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail("A prohibited action reached the model intent classifier")

    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    monkeypatch.setattr("agent.intent_router._ask_intent_model", unexpected)
    assert classify_request(text) is None
    assert asyncio.run(resolve_request(text)) is None
    assert asyncio.run(route_request(text)).intent == "conversation"
    assert route_operational_request(text) is None


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Show me how much memory you're using.", "ram_usage"),
        ("How much RAM is Curie using?", "ram_usage"),
        ("Please check the weather in Paris.", "weather"),
        ("Have a look at my calendar for tomorrow.", "calendar_list"),
        (
            "Could you check whether I need an umbrella in Hong Kong tomorrow?",
            "weather",
        ),
        ("Can you gather some recent news about local AI?", "research"),
        ("Please check Curie's health.", "dashboard_snapshot"),
        ("Check if Curie is running properly.", "dashboard_snapshot"),
    ],
)
def test_fresh_conversational_requests(text, action):
    request = classify_request(text)
    assert request is not None and request.action == action
