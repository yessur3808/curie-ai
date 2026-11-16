from agent.personality_context import PersonalityContext
from utils.persona import load_persona
import pytest


def context():
    return PersonalityContext(load_persona("curie.json"))


def test_live_and_regular_prompts_offer_contextual_french():
    c = context()
    for build in (c.build_live_prompt_directives, c.build_compact_prompt_directives):
        prompt = " ".join(build("I finally worked out why the coffee tasted bitter."))
        assert "English dominant" in prompt
        assert "French is part of your voice, not a quota" in prompt
        assert "only when greeting" in prompt
        assert "redundant translation" in prompt.lower()


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
    command = c._french_turn_directive("Do it", {"interaction_kind": "command"})
    assert "verified result first" in command
    assert "French expression is optional" in command
    assert "never add a decorative suffix" in command


def test_other_personalities_do_not_inherit_french():
    assert (
        PersonalityContext({"name": "Other"})._french_turn_directive(
            "Hello", {"mode": "casual"}
        )
        == ""
    )


def test_recent_french_rule_also_covers_corrections():
    result = context()._french_turn_directive(
        "Correction: I sent it already.",
        {"interaction_kind": "correction"},
        [("assistant", "Voilà. That fits.")],
    )
    assert "avoid another" in result


def test_full_and_compact_preserve_the_same_naturalness_policy():
    c = context()
    text = "I bought a blue notebook. I liked the colour."
    expected = c._natural_conversation_directives(c.infer_runtime_context(text))
    assert len(expected) == 2
    for build in (c.build_prompt_directives, c.build_compact_prompt_directives):
        directives = build(text)
        for rule in expected:
            assert directives.count(rule) == 1


def test_neutral_preference_rules_do_not_override_commands_or_urgency():
    assert (
        context()._natural_conversation_directives(
            {"mode": "urgent", "interaction_kind": "command"}
        )
        == []
    )


def test_wit_guidance_has_no_copyable_unrelated_joke():
    prompt = " ".join(
        context().build_compact_prompt_directives("I prefer tea to coffee.")
    )
    assert "bugs can wait" not in prompt
    assert "Ground any wit in the current conversation" in prompt


@pytest.mark.parametrize(
    "text",
    [
        "I bought a notebook because I liked the colour.",
        "I like rainy afternoons and coffee.",
        "Ask me a relaxed question about my book.",
    ],
)
def test_ordinary_chat_is_not_given_a_french_quota(text):
    result = context()._french_turn_directive(text, {"mode": "casual"})
    assert "reply in English without a French aside" in result


@pytest.mark.parametrize(
    "text",
    [
        "Could you answer in French?",
        "Speak French please.",
        "Respond in French even though the last reply was French.",
    ],
)
def test_explicit_french_request_wins_over_recent_touch(text):
    result = context()._french_turn_directive(
        text, {"mode": "casual"}, [("assistant", "Bonjour.")]
    )
    assert "explicit request for French" in result


def test_actual_workflow_carries_shared_naturalness_rules(monkeypatch):
    from agent.chat_workflow import ChatWorkflow

    monkeypatch.setattr(
        "services.current_awareness.awareness_context", lambda *a, **k: None
    )
    monkeypatch.setattr("services.x_research.research_context", lambda *a, **k: "")
    monkeypatch.setattr("services.knowledge_library.recall", lambda *a, **k: [])
    workflow = ChatWorkflow(persona=load_persona("curie.json"))
    prompt = workflow._build_structured_prompt(
        {},
        [],
        "I bought a blue notebook. I liked the colour.",
        internal_id="voice-policy-regression",
        adaptive_memories=[],
        matching_abilities=[],
        memory_conflicts=[],
    )
    system = prompt.messages[0]["content"]
    assert system.count("Neutral-plan delivery") == 1
    assert (
        "ordinary preferences, purchases and plans do not need your approval" in system
    )
    assert "reply in English without a French aside" in system
    assert "Prefer a meaningful French touch" not in system
    assert (
        prompt.messages[-1]["content"]
        == "I bought a blue notebook. I liked the colour."
    )
