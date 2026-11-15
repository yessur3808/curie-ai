import json

from agent.personality_adapter import PersonalityAdapter
from agent.personality_speech import PersonalitySpeechEngine
from utils.persona import load_persona


def test_greeting_is_social_depth():
    context = PersonalityAdapter().infer_context("Hi Curie, how are you doing?")
    assert context["response_depth"] == "social"


def test_simple_question_is_brief_depth():
    context = PersonalityAdapter().infer_context("What time is it?")
    assert context["response_depth"] == "brief"


def test_everyday_opinion_with_a_reason_stays_brief():
    context = PersonalityAdapter().infer_context(
        "Carbonara or arrabbiata? Pick one and tell me why."
    )

    assert context["interaction_kind"] == "explanation"
    assert context["response_depth"] == "brief"


def test_technical_choice_still_gets_focused_depth():
    context = PersonalityAdapter().infer_context(
        "Pick one database architecture and tell me why."
    )

    assert context["response_depth"] == "focused"


def test_complex_request_is_focused_depth():
    context = PersonalityAdapter().infer_context(
        "Compare these database designs and recommend one"
    )
    assert context["response_depth"] == "focused"


def test_explicit_detail_request_is_deep_depth():
    context = PersonalityAdapter().infer_context(
        "Give me a detailed step-by-step explanation"
    )
    assert context["response_depth"] == "deep"


def test_technical_request_stays_casual_and_precise_by_default():
    context = PersonalityAdapter().infer_context("Debug this Python API error")

    assert context["user_emotion"] == "technical"
    assert context["mode"] == "casual"
    assert context["response_depth"] == "focused"


def test_explicit_formal_request_uses_professional_mode():
    context = PersonalityAdapter().infer_context(
        "Write a formal report about the database migration"
    )

    assert context["mode"] == "professional"
    assert context["explicitly_formal"] is True


def test_simple_command_is_brief_even_with_detailed_preference():
    context = PersonalityAdapter().infer_context(
        "Please turn off the DreamView",
        {"_adaptation": {"verbosity": "detailed"}},
    )

    assert context["interaction_kind"] == "command"
    assert context["response_depth"] == "brief"


def test_social_style_preserves_meaning_without_forced_french():
    persona = load_persona("curie.json")
    response = (
        "The missing piece turned up at last. Under the sofa, of course. "
        "Was it the last gap in the puzzle?"
    )
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "casual", "response_depth": "social"}
    )
    assert result == response
    assert "C'est magnifique" not in result


def test_deep_reply_is_not_rewritten_to_force_french():
    persona = load_persona("curie.json")
    response = "First point is clear. Second point follows. Third point matters. Fourth point confirms it."
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "professional", "response_depth": "deep"}
    )
    assert result == response


def test_urgent_reply_is_not_modified_for_personality_frequency():
    persona = load_persona("curie.json")
    response = "Call emergency services now. Move away from the fire."
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "urgent", "response_depth": "brief"}
    )
    assert result == response


def test_deep_reply_is_not_trimmed():
    persona = load_persona("curie.json")
    response = (
        "First paragraph. Second paragraph. Third paragraph with necessary detail."
    )
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "professional", "response_depth": "deep"}
    )
    assert "third paragraph" in result.lower()


def test_brief_style_does_not_discard_later_requested_items():
    persona = load_persona("curie.json")
    response = (
        "The first train leaves at 08:20. The second leaves at 09:10. "
        "The second is cheaper, but it gets you there after your meeting starts. "
        "Choose the first if the meeting time is fixed."
    )
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "casual", "response_depth": "brief"}
    )
    assert result == response


def test_curie_persona_is_source_independent_and_balanced():
    persona = load_persona("curie.json")
    serialized = json.dumps(persona).lower()
    assert "fallout" not in serialized
    casual = persona["style_modulation"]["casual"]
    assert 0.25 <= casual["humor_level"] <= 0.5
    assert 0.5 <= casual["care_level"] <= 0.7
    assert "relaxed" in casual["formality"]
    assert "do not force one into every answer" in serialized
    assert "brief, meaningful french expression" in serialized
    assert "result or blocker first" in serialized
    assert "deeply valued person" not in serialized
