import asyncio

import pytest

from agent.conversation_contract import (
    bare_casual_update,
    trim_bare_choice,
    trim_idle_question,
)
from agent.orchestration.response_policy import ResponsePolicy
from agent.orchestration.social_service import SocialConversationService
from agent.persona_contract import active_persona
from agent.personality_adapter import PersonalityAdapter
from agent.personality_context import PersonalityContext
from agent.response_planner import plan_response
from agent.routing import route_request


@pytest.mark.parametrize(
    "text",
    [
        "Thanks, that helped.",
        "Thank you! That worked.",
        "Thanks a lot, that makes sense.",
        "Cheers, much appreciated.",
        "Thanks. That’s helpful!",
    ],
)
def test_closed_gratitude_uses_consistent_fast_path_without_question(text):
    candidate = SocialConversationService().handle(text)
    assert candidate.text == "Anytime."
    assert asyncio.run(route_request(text)).intent == "social"
    assert plan_response(text)["interaction"] == "social"
    assert PersonalityAdapter().infer_context(text)["interaction_kind"] == "social"
    persona = active_persona()
    assert (
        ResponsePolicy(persona, PersonalityContext(persona)).finalize(
            candidate.text, text
        )
        == "Avec plaisir."
    )


@pytest.mark.parametrize(
    "text",
    [
        "Thanks, now turn the lights off.",
        "Thanks, that helped. Why did it fail?",
        "Thanks, that didn't help.",
        "Thanks, that helped but I still need help.",
        "Thank you for nothing.",
        "Thanks, that helped?",
        "Thank you. Please remind me tomorrow.",
    ],
)
def test_gratitude_does_not_swallow_requests_or_negative_feedback(text):
    assert SocialConversationService().handle(text) is None


@pytest.mark.parametrize(
    "text,raw,expected",
    [
        (
            "Thanks, that helped.",
            "Glad to hear it. What’s next on your mind?",
            "Glad to hear it.",
        ),
        (
            "I prefer tea to coffee.",
            "D'accord. Tea it is. Earl Grey or something herbal?",
            "D'accord. Tea it is.",
        ),
        (
            "English only: I found the missing piece.",
            "That is a relief. What was it?",
            "That is a relief.",
        ),
    ],
)
def test_idle_questions_removed_without_extra_inference(text, raw, expected):
    assert trim_idle_question(raw, text) == expected


@pytest.mark.parametrize(
    "text,reply",
    [
        ("I prefer tea. Ask me about it.", "Tea it is. Which kind?"),
        ("I prefer tea, but need advice.", "I can help. Which kind?"),
        ("Help me choose a tea.", "Which kind do you like?"),
        ("Delete the file.", "This is irreversible. Confirm deletion?"),
        ("I found a gun.", "Keep away from it. Are you somewhere safe?"),
        ("I found the API key.", "Keep it private. Has it been exposed?"),
        ("I like tea.", "What kind?"),
        ("I like tea.", 'The label says "Ready. Drink?"'),
        ("I like tea.", "```Ready. Drink?```"),
        ("I like tea.", "First line.\nSecond line?"),
        ("Thanks.", "The file name is ambiguous. Which file?"),
    ],
)
def test_boundaries_preserve_real_questions_and_payloads(text, reply):
    assert trim_idle_question(reply, text) == reply


def test_update_directive_and_grounding_reach_every_prompt_path():
    context = PersonalityContext(active_persona())
    assert bare_casual_update("I found the missing puzzle piece under the sofa.")
    for method in (
        context.build_prompt_directives,
        context.build_live_prompt_directives,
        context.build_compact_prompt_directives,
    ):
        directives = " ".join(method("I prefer tea to coffee."))
        assert "partial progress is not completion" in directives
        assert "stop. Do not append a question" in directives


def test_other_personas_are_not_given_curie_question_policy():
    class Identity:
        def apply_response_style(self, response, *args, **kwargs):
            return response

    raw = "Tea it is. Which kind?"
    assert (
        ResponsePolicy({"name": "Andreja"}, Identity()).finalize(raw, "I prefer tea.")
        == raw
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Another puzzle. The sofa won’t finish itself.", "Another puzzle."),
        ("A book. You've earned a rest.", "A book."),
        ("D’accord. A book. You've earned a rest.", "D’accord. A book."),
        ("Tea and a book.", "Tea and a book."),
    ],
)
def test_bare_choice_has_no_unrequested_coda(raw, expected):
    assert (
        trim_bare_choice(raw, "Tea and a book or another puzzle tonight? Pick one.")
        == expected
    )


@pytest.mark.parametrize(
    "question,reply",
    [
        ("Tea or coffee? Explain why and pick one.", "Tea. It has less caffeine."),
        ("Which investment? Pick one.", "I cannot choose for you. Consider the risks."),
        ("Which file to delete? Pick one.", "I need more information. Name the file."),
        (
            "Tea or coffee? Pick one.",
            "I can't choose safely. You mentioned an allergy.",
        ),
        ("Tea or coffee? Pick one.", "Are there any allergies?"),
        ("Tea or coffee? Pick one.", "Tea. Avoid it if you have an allergy."),
        ("Which road? Pick one.", "The main road. The other is flooded."),
        ("Tea or coffee? Pick one.", '"Tea. Coffee."'),
    ],
)
def test_choice_contract_preserves_explanations_safety_and_payloads(question, reply):
    assert trim_bare_choice(reply, question) == reply
