"""Production regressions with varied wording; no expected answer lookup table."""

import asyncio

import httpx
import pytest

from agent.intent_router import classify_request, resolve_request
from agent.routing import route_request
from agent.kernel.dialogue_state import classify_transition, TransitionClass
from agent.tooling.errors import ProviderAccessError, user_facing_tool_error
from connectors.twitter import _check_response


@pytest.mark.parametrize(
    "text",
    [
        "What is the error?",
        "What caused that failure?",
        "Why did it stop?",
        'You previously mentioned "The x search tool ran into an internal error and stopped." Thus I was verifying the error',
        "You said 'turn off both lights' failed. What happened?",
        "You just reported that the calendar search failed. Explain.",
        "Why would you check previous dates?",
        "No, we're already on October 25. Why would you offer a forecast for past dates?",
    ],
)
def test_explanations_do_not_reexecute_quoted_actions(text):
    history = [{"role": "assistant", "content": "The X search failed."}]
    assert classify_request(text, history) is None
    assert asyncio.run(resolve_request(text, history)) is None
    assert asyncio.run(route_request(text, history=history)).intent == "conversation"
    assert (
        classify_transition(text, history=history).kind is TransitionClass.CONTINUATION
    )


@pytest.mark.parametrize(
    "text",
    [
        'Search Twitter for "AI setup"',
        "/x search AI setup",
        "Turn off both lights",
        "Check the weather in Hong Kong",
    ],
)
def test_real_requests_still_reach_the_executor(text):
    assert classify_request(text) is not None


def test_specific_search_source_does_not_compete_with_generic_news_intent():
    result = classify_request("Search Twitter for current local AI setup news")
    assert result.action == "x_search"


@pytest.mark.parametrize(
    "text", ["Which option?", "Make it warmer", "Why?", "What about the blue one?"]
)
def test_references_keep_the_exchange(text):
    history = [
        {
            "role": "assistant",
            "content": "The blue option is quieter, the red one is faster.",
        }
    ]
    assert (
        classify_transition(text, history=history).kind is TransitionClass.CONTINUATION
    )


def test_explicit_topic_change_does_not_inherit_failed_search():
    history = [{"role": "assistant", "content": "The X search failed."}]
    assert (
        classify_transition("New topic: what should I cook?", history=history).kind
        is TransitionClass.NEW_GOAL
    )


@pytest.mark.parametrize("city", ["Osaka", "Lisbon", "New York"])
def test_corrected_location_runs_a_real_lookup(city):
    history = [
        {"role": "user", "content": "What's the weather in Kyoto?"},
        {"role": "assistant", "content": "Rain, 18C."},
    ]
    request = classify_request(f"Actually I meant {city}. Check that instead.", history)
    assert request.action == "weather"
    assert request.params["query"] == "Weather in " + city
    assert classify_request(f"Actually I meant {city}. Check that instead.") is None


def test_conversation_pressure_does_not_silently_replace_the_model(tmp_path):
    from services.resource_governor import ResourceGovernor

    decision = ResourceGovernor(tmp_path / "budget.json").admit(
        "conversation",
        "owner",
        256,
        preferred_model="general",
        fallback_model="fast",
        diagnostics={"p95_latency_ms": 9000, "last": {"rss_end_mb": 5500}},
    )
    assert decision.allowed and decision.model == "general"
    assert not decision.downgraded and decision.max_tokens == 256


def test_technical_decision_is_not_mistaken_for_code_generation():
    from agent.orchestration.model_service import ModelConversationService

    assert (
        ModelConversationService.model_role(
            "Our API p95 is 14 seconds. Would replacing the database be your first step?"
        )
        == "reasoning"
    )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authorization_failed"),
        (402, "credits_depleted"),
        (403, "access_denied"),
        (429, "rate_limited"),
        (503, "provider_error"),
    ],
)
def test_provider_errors_are_specific_and_secret_safe(status, code):
    response = httpx.Response(
        status,
        json={
            "type": "https://api.x.com/2/problems/credits-depleted",
            "detail": "private-provider-body",
        },
        request=httpx.Request("GET", "https://example.com/?token=private-query"),
    )
    with pytest.raises(ProviderAccessError) as caught:
        _check_response(response)
    assert caught.value.error_code == code
    assert caught.value.http_status == status
    assert str(status) in user_facing_tool_error(caught.value)
    assert "private" not in str(caught.value) + user_facing_tool_error(caught.value)
    assert caught.value.retryable == (status == 429 or status >= 500)
