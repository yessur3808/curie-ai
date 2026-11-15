import asyncio

import pytest

from agent.intent_router import classify_request
from agent.orchestration.model_service import ModelConversationService
from agent.orchestration.response_policy import ResponsePolicy
from agent.orchestration.social_service import SocialConversationService
from agent.routing import route_request


@pytest.mark.parametrize(
    "prefix", ["No rain.", "Not raining;", "It's hot and stuffy, but no rain."]
)
def test_weather_preface_preserves_explicit_action(prefix):
    text = prefix + " Turn off the desk lamp."
    assert SocialConversationService().handle(text) is None
    request = classify_request(text)
    assert request.action == "home_control"
    assert request.params["target"] == "desk lamp"
    assert request.params["state"] == "off"
    decision = asyncio.run(route_request(text, allow_model_inference=False))
    assert decision.selected_capability == "home_control"


@pytest.mark.parametrize(
    "tail",
    [
        "Do not turn off the lamp.",
        "Explain how you would turn off the lamp.",
        '"Turn off the lamp."',
        "I might turn off the lamp.",
        "If I asked you to turn off the lamp, would you?",
        "I'm upset about my interview.",
        "Tell me a joke.",
    ],
)
def test_weather_does_not_consume_followups_or_authorize_quoted_actions(tail):
    text = "No rain. " + tail
    assert SocialConversationService().handle(text) is None
    assert classify_request(text) is None


def test_weather_preface_does_not_bypass_group_approval():
    request = classify_request("No rain. Turn off all lights.")
    assert request.action == "home_control"
    assert request.needs_approval


@pytest.mark.parametrize(
    "text,role",
    [
        (
            "I found a typo after closing the laptop. Give me one dry joke about that, not a plan.",
            "general",
        ),
        ("Keep me company, no research please.", "general"),
        ("Explain API latency without code.", "reasoning"),
        ("Not a plan: implement the Python function.", "coding"),
        ("Compare two database architectures, not a plan.", "reasoning"),
    ],
)
def test_negated_output_type_does_not_choose_specialist(text, role):
    assert ModelConversationService.model_role(text) == role


class IdentityStyle:
    def apply_response_style(self, response, *_args, **_kwargs):
        return response


@pytest.mark.parametrize("joiner", [";", ":", "—", "–", ","])
def test_removed_french_suffix_does_not_leave_dangling_joiner(joiner):
    policy = ResponsePolicy({"name": "Curie"}, IdentityStyle())
    raw = f"The total is $12.50{joiner} *voilà*."
    assert policy.finalize(raw, "What is the total?") == "The total is $12.50."


def test_suffix_cleanup_preserves_internal_punctuation_and_exact_output():
    policy = ResponsePolicy({"name": "Curie"}, IdentityStyle())
    raw = "Today: 19–27°C; Monday: 21–25°C; voilà."
    assert (
        policy.finalize(raw, "Show the temperatures.")
        == "Today: 19–27°C; Monday: 21–25°C."
    )
    assert policy.finalize(raw, "Repeat verbatim.") == raw
