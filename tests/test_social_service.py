import asyncio

from agent.orchestration.social_service import SocialConversationService
from agent.routing import route_request


def test_technical_win_gets_brief_banter_and_grounded_next_step():
    candidate = SocialConversationService().handle(
        "I finally fixed a stubborn bug. Give me brief banter and one next step."
    )

    assert candidate is not None
    assert "far too comfortable" in candidate.text
    assert "regression test" in candidate.text
    assert "commit the fix" in candidate.text


def test_simple_corrections_and_preferences_are_natural_and_deterministic():
    service = SocialConversationService()

    assert service.handle("There is no project").text == (
        "You're right. I made an assumption there."
    )
    assert service.handle("There is none").text == "Understood."
    assert (
        service.handle("I'm working now, and don't need lights on during the day").text
        == "Got it. They can stay off."
    )


def test_technical_win_routes_without_model_inference():
    decision = asyncio.run(route_request("I finally fixed that stubborn bug."))

    assert decision.intent == "social"


def test_deep_bug_followup_stays_available_to_normal_reasoning():
    candidate = SocialConversationService().handle(
        "I fixed the bug. Explain why the race condition happened."
    )

    assert candidate is None


def test_short_banter_gets_one_fast_advice_free_sentence():
    candidate = SocialConversationService().handle(
        "That was almost suspiciously efficient."
    )

    assert candidate is not None
    assert candidate.text == "I prefer ‘efficient enough to raise questions.’"
    assert candidate.text.count(".") == 1


def test_short_banter_routes_without_model_inference():
    decision = asyncio.run(route_request("That was almost suspiciously efficient."))

    assert decision.intent == "social"
