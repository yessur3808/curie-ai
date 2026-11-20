from agent.orchestration.model_service import ModelConversationService
from llm.chat_prompt import conversation_prompt
import pytest


@pytest.mark.parametrize(
    "text",
    [
        "I chose the museum because it was next to the bus stop, not for any deep reason.",
        "I bought it for no particular reason.",
        "The reason I like it is the colour.",
    ],
)
def test_incidental_reason_is_not_a_reasoning_task(text):
    assert ModelConversationService.model_role(text) == "general"


@pytest.mark.parametrize(
    "text", ["Reason through these constraints", "Reason about this paradox"]
)
def test_explicit_reasoning_request_keeps_specialist(text):
    assert ModelConversationService.model_role(text) == "reasoning"


def test_reasoning_followup_retains_specialist_but_new_topic_does_not():
    history = [
        (
            "user",
            "Our API p95 is 14 seconds. Would replacing the database be your first step?",
        ),
        ("assistant", "No, measure the request path first."),
    ]
    user = "What would you measure first then?"
    prompt = conversation_prompt("Be Curie", history, user)
    assert ModelConversationService.contextual_role(user, prompt) == "reasoning"
    user = "New topic: what should I wear?"
    prompt = conversation_prompt("Be Curie", history, user)
    assert ModelConversationService.contextual_role(user, prompt) == "general"


def test_does_not_inherit_reasoning_for_a_self_contained_unrelated_question():
    user = "How tall is a giraffe?"
    prompt = conversation_prompt("Be Curie", [("user", "Analyze API latency")], user)
    assert ModelConversationService.contextual_role(user, prompt) == "general"


def test_incidental_code_in_an_activity_choice_is_not_a_programming_task():
    text = "I'm tired after work. Pick one: tea and a book or another hour fixing code. Keep it casual."
    assert ModelConversationService.model_role(text) == "general"


def test_service_error_explanation_is_not_a_coding_request():
    text = "New topic: an X search returned HTTP 402, API credits depleted. What does that mean? Don't retry it."
    assert ModelConversationService.model_role(text) == "general"
