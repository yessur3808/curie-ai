import pytest

from agent.personality_context import PersonalityContext


@pytest.mark.parametrize(
    "builder", ["build_prompt_directives", "build_compact_prompt_directives"]
)
def test_curie_wit_is_optional_and_bounded(builder):
    context = PersonalityContext({"name": "Curie"})
    rules = "\n".join(getattr(context, builder)("I finally organized my desk."))
    assert "Jarvis-like" in rules
    assert "Most replies need neither" in rules
    assert "at most one" in rules
    assert "never mock the user" in rules
    assert "approval requests" in rules
    assert "not the person" in rules


@pytest.mark.parametrize(
    "builder", ["build_prompt_directives", "build_compact_prompt_directives"]
)
@pytest.mark.parametrize(
    "text",
    [
        "I'm scared and overwhelmed.",
        "The tool failed again.",
        "/approve action deadbeef",
        "Please check my calendar.",
        "Give me a formal report.",
        "No jokes please.",
        "I need medical advice.",
        "That is not what I meant.",
    ],
)
def test_sensitive_turns_suppress_wit(builder, text):
    rules = "\n".join(getattr(PersonalityContext({"name": "Curie"}), builder)(text))
    assert "This turn: omit sarcasm and teasing" in rules
    assert "Occasionally add" not in rules


def test_other_personas_unchanged():
    rules = "\n".join(
        PersonalityContext({"name": "Andreja"}).build_compact_prompt_directives("Hello")
    )
    assert "Jarvis-like" not in rules
    assert "Curie follow-ups" not in rules
