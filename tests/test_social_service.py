import asyncio
import pytest

from agent.orchestration.social_service import SocialConversationService


def test_standalone_repetition_complaint_does_not_regenerate_the_old_news():
    result = SocialConversationService().handle(
        "Hey Curie, you already shared Letta agents with me."
    )
    assert result.model_used == "social:repetition_feedback"
    assert result.text == "You’re right—that was a repeat, not a useful update."


@pytest.mark.parametrize(
    "text",
    [
        "You already shared this. What changed since then?",
        "You already shared this, but send me the source.",
        "You already shared this, explain why.",
        "You already shared this, please check the new release.",
        "You already shared this, I need more detail.",
        "You already shared this, turn off the lamp.",
        "You already shared this so stop sending it.",
    ],
)
def test_repetition_feedback_does_not_swallow_a_followup(text):
    assert SocialConversationService().handle(text) is None


from agent.routing import route_request


def test_technical_win_uses_contextual_generation_for_requested_banter_and_advice():
    candidate = SocialConversationService().handle(
        "I finally fixed a stubborn bug. Give me brief banter and one next step."
    )

    assert candidate is None


def test_corrections_and_preferences_are_not_answered_without_context():
    service = SocialConversationService()

    assert service.handle("There is no project") is None
    assert service.handle("There is none") is None
    assert (
        service.handle("I'm working now, and don't need lights on during the day")
        is None
    )


def test_technical_win_routes_to_conversation_without_classifier_inference():
    decision = asyncio.run(
        route_request("I finally fixed that stubborn bug.", allow_model_inference=False)
    )

    assert decision.intent == "conversation"


def test_deep_bug_followup_stays_available_to_normal_reasoning():
    candidate = SocialConversationService().handle(
        "I fixed the bug. Explain why the race condition happened."
    )

    assert candidate is None


def test_banter_is_generated_in_context_not_selected_from_stock_lines():
    candidate = SocialConversationService().handle(
        "That was almost suspiciously efficient."
    )

    assert candidate is None


def test_short_banter_routes_to_contextual_conversation():
    decision = asyncio.run(
        route_request(
            "That was almost suspiciously efficient.", allow_model_inference=False
        )
    )

    assert decision.intent == "conversation"


@pytest.mark.parametrize(
    "text",
    [
        "I haven't fixed the bug yet.",
        "I fixed the bug, but now the app crashes.",
        "I nearly solved the issue.",
        "The error isn't resolved.",
        "That was not what I asked.",
        "That was awful. Why did you ignore the question?",
        "There is no project. Could we discuss something else?",
    ],
)
def test_negation_partial_progress_and_feedback_never_get_canned_success(text):
    assert SocialConversationService().handle(text) is None
    decision = asyncio.run(route_request(text, allow_model_inference=False))
    assert decision.intent == "conversation"


def test_french_check_in_is_brief_natural_and_deterministic():
    candidate = SocialConversationService().handle("Ca va?")

    assert candidate is not None
    assert candidate.text == "Ça va bien, merci. Et toi?"
    assert candidate.model_used == "social:french_check_in"
    assert asyncio.run(route_request("Ça va? ")).intent == "social"


def test_weather_and_no_tool_corrections_get_brief_human_acknowledgements():
    service = SocialConversationService()

    weather = service.handle("No rain, quite hot and stuffy actually")
    no_query = service.handle("No need to use any query")

    assert weather is not None
    assert weather.text == "Oof. That kind of stuffy heat is miserable."
    assert weather.model_used == "social:weather_correction"
    assert no_query is not None
    assert no_query.text == "Got it. No query needed."
    assert no_query.model_used == "social:no_tool_acknowledgement"
    assert (
        asyncio.run(route_request("No rain, quite hot and stuffy actually")).intent
        == "social"
    )


def test_natural_presence_weather_update_and_close_use_the_social_fast_path():
    service = SocialConversationService()

    presence = service.handle("Bonjour, you alive in there?")
    weather = service.handle(
        "I'm surviving. It's hot and stuffy here, but at least there is no rain."
    )
    closing = service.handle("That'll do, merci.")

    assert presence.text == "Bonjour. Awake and ready."
    assert weather.text == "Oof. That kind of stuffy heat is miserable."
    assert closing.text == "Anytime."
