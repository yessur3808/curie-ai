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


def test_social_reply_is_trimmed_without_forced_french():
    persona = load_persona("curie.json")
    response = (
        "I am functioning optimally this evening. My systems are humming with curiosity. "
        "The air is crisp and I am eager to learn something new. How about you?"
    )
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "casual", "response_depth": "social"}
    )
    assert (
        result
        == "I am functioning optimally this evening. My systems are humming with curiosity."
    )
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


def test_brief_reply_is_hard_bounded():
    persona = load_persona("curie.json")
    response = " ".join(["A rather unnecessary explanation continues here."] * 30)
    result = PersonalitySpeechEngine().apply(
        response, persona, {"mode": "casual", "response_depth": "brief"}
    )
    assert len(result.split()) <= 85


def test_curie_persona_is_source_independent_and_balanced():
    persona = load_persona("curie.json")
    serialized = json.dumps(persona).lower()
    assert "fallout" not in serialized
    casual = persona["style_modulation"]["casual"]
    assert 0.25 <= casual["humor_level"] <= 0.5
    assert 0.5 <= casual["care_level"] <= 0.7
    assert "relaxed" in casual["formality"]
