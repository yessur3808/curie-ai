from agent.personality_context import PersonalityContext
from utils.persona import load_persona


def context():
    return PersonalityContext(load_persona("curie.json"))


def test_live_and_regular_prompts_offer_contextual_french():
    c = context()
    for build in (c.build_live_prompt_directives, c.build_compact_prompt_directives):
        prompt = " ".join(build("I finally worked out why the coffee tasted bitter."))
        assert "one fitting French touch" in prompt
        assert "d'accord" in prompt
        assert "No redundant translation" in prompt


def test_recent_french_is_not_repeated():
    c = context()
    result = c._french_turn_directive(
        "Tell me more",
        {"mode": "casual"},
        [{"assistant": "D’accord. Let's look at that."}],
    )
    assert "avoid another" in result
    assert "one fitting" not in result


def test_language_and_task_constraints_win():
    c = context()
    assert "do not add French" in c._french_turn_directive(
        "Reply in English only", {"mode": "casual"}
    )
    assert "do not add French" in c._french_turn_directive(
        "Use this exact wording", {"mode": "casual"}
    )
    assert "factual details" in c._french_turn_directive("Help", {"mode": "urgent"})
    assert "factual details" in c._french_turn_directive(
        "Do it", {"interaction_kind": "command"}
    )


def test_other_personalities_do_not_inherit_french():
    assert (
        PersonalityContext({"name": "Other"})._french_turn_directive(
            "Hello", {"mode": "casual"}
        )
        == ""
    )
