import pytest
from services.trained_voice.action_boundary import unavailable_action_response


@pytest.mark.parametrize(
    "text",
    [
        "Turn off the desk lamp",
        "Different topic: switch off the desk lamp.",
        "What's the weather in Kyoto?",
    ],
)
def test_unsupported_dashboard_actions_cannot_be_invented_by_the_model(text):
    reply = unavailable_action_response(text)
    assert reply and "haven't" in reply and "Telegram" in reply


@pytest.mark.parametrize(
    "text",
    [
        "What is the error?",
        "You said turn off the lamp. Explain what happened.",
        "Draft two short notes to Mina. Don't send them.",
        "Make the second note warmer.",
    ],
)
def test_normal_conversation_still_reaches_the_model(text):
    assert unavailable_action_response(text) is None


def test_supplied_error_report_is_explanation_not_a_new_search():
    text = "New topic: an X search just returned HTTP 402, API credits depleted. What does that mean? Don't retry it."
    assert unavailable_action_response(text) is None
