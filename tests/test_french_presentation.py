import pytest

from agent.french_presentation import contextual_touch, recent_assistant_messages
from agent.orchestration.response_policy import ResponsePolicy
from agent.persona_contract import active_persona
from agent.personality_context import PersonalityContext


def policy():
    persona = active_persona()
    return ResponsePolicy(persona, PersonalityContext(persona))


@pytest.mark.parametrize("history", [[], [("assistant", "D’accord. Tea it is.")]])
def test_goodnight_does_not_become_a_greeting(history):
    assert (
        policy().finalize(
            "Bonsoir. Sleep well; Pip can guard the bottle caps.",
            "Thanks. Goodnight.",
            history=history,
        )
        == "Sleep well; Pip can guard the bottle caps."
    )


def test_evening_greeting_keeps_its_meaning_when_translated():
    assert (
        policy().finalize("Bonsoir. Good to see you.", "Reply in English only, please.")
        == "Good evening. Good to see you."
    )


@pytest.mark.parametrize(
    "question,raw,expected",
    [
        ("Hello Curie", "Hello. Good to see you.", "Bonjour. Good to see you."),
        ("Thanks", "Anytime.", "Avec plaisir."),
        ("I prefer tea.", "Got it. Tea it is.", "D’accord. Tea it is."),
        ("I found the missing piece.", "There we go. It fits.", "Voilà. It fits."),
        (
            "The pattern repeats.",
            "Interesting. It repeats every 12 days.",
            "C’est intéressant. It repeats every 12 days.",
        ),
    ],
)
def test_meaningful_surface_touch_without_inference(
    monkeypatch, question, raw, expected
):
    import llm.manager

    def forbidden(*args, **kwargs):
        raise AssertionError("Presentation must not call a model")

    monkeypatch.setattr(llm.manager, "ask_llm", forbidden)
    assert policy().finalize(raw, question) == expected


@pytest.mark.parametrize(
    "history",
    [
        [{"assistant": "Bonjour."}, {"assistant": "It is 12 days."}],
        [("assistant", "Bonjour."), ("user", "Why?"), ("assistant", "It is 12 days.")],
        [
            {"role": "assistant", "content": "Bonjour."},
            {"role": "assistant", "content": "It is 12 days."},
        ],
    ],
)
def test_older_different_french_does_not_silence_current_touch(history):
    assert policy().finalize("Anytime.", "Thanks", history=history) == "Avec plaisir."
    assert (
        policy().finalize("Voilà. It fits.", "I found the piece.", history=history)
        == "Voilà. It fits."
    )
    directive = PersonalityContext(active_persona())._french_turn_directive(
        "Thanks", {"mode": "casual"}, history
    )
    assert "Prefer one fitting" in directive


def test_adjacent_or_same_recent_expression_is_not_injected():
    assert (
        policy().finalize("Anytime.", "Thanks", history=[{"assistant": "Bonjour."}])
        == "Anytime."
    )
    assert (
        policy().finalize(
            "Anytime.",
            "Thanks",
            history=[{"assistant": "Avec plaisir."}, {"assistant": "It is 12 days."}],
        )
        == "Anytime."
    )
    # The request guidance understands the same history encodings as finalization.
    for history in (
        [("assistant", "Bonjour.")],
        [{"role": "assistant", "content": "Bonjour."}],
    ):
        assert "avoid another" in PersonalityContext(
            active_persona()
        )._french_turn_directive("Thanks", {"mode": "casual"}, history)


@pytest.mark.parametrize(
    "question",
    [
        "English-only: thanks",
        "No French, thanks",
        "Repeat verbatim",
        "Reply with exactly two words",
        "Write a letter",
        "Translate into Spanish",
        "Answer in Spanish",
        "Give JSON only",
        "Urgent: thanks",
        "I am overwhelmed",
        "Check the lamp",
        "That is not what I meant",
        "Explain the API error",
        "Use a formal tone",
        "My chest pain is getting worse",
        "I need medical advice",
    ],
)
def test_surface_fallback_respects_constraints(question):
    assert (
        contextual_touch("Got it. I understand.", question, active_persona())
        == "Got it. I understand."
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"reply":"Got it."}',
        '```python\nprint("Got it.")\n```',
        '"Got it." is the quotation.',
        "Got it. `amount = 12`",
        "Got it. https://example.org/?x=12",
        "Got it.\n- 12°C\n- 80%",
        "Bonjour. Good to see you.",
        "The lamp is off.",
    ],
)
def test_protected_content_and_nonmatching_prose_untouched(raw):
    assert contextual_touch(raw, "Thanks", active_persona()) == raw


def test_other_persona_disabled_config_and_idempotence():
    assert contextual_touch("Anytime.", "Thanks", {"name": "Andreja"}) == "Anytime."
    assert contextual_touch("Anytime.", "Thanks", {"name": "Curie"}) == "Anytime."
    first = policy().finalize("Anytime.", "Thanks")
    assert policy().finalize(first, "Thanks") == first
    assert recent_assistant_messages([{"user": "Bonjour"}]) == []


def test_deterministic_social_path_uses_same_presentation():
    from agent.orchestration.social_service import SocialConversationService

    reply = SocialConversationService().handle("Thanks")
    assert policy().finalize(reply.text, "Thanks") == "Avec plaisir."


@pytest.mark.parametrize(
    "opening", ["Bonjour", "D'accord", "Avec plaisir", "Merci", "C’est intéressant"]
)
def test_model_openings_honor_english_only_and_adjacent_history(opening):
    raw = opening + ". The result is 18 of 20."
    for question, history in [
        ("English only: give the result", []),
        ("Give the result", [("assistant", "Bonjour.")]),
    ]:
        result = policy().finalize(raw, question, history=history)
        assert opening not in result
        assert result.endswith("The result is 18 of 20.")


def test_explicit_french_request_overrides_surface_cooldown():
    assert (
        policy().finalize(
            "Avec plaisir.", "Reply in French", history=[("assistant", "Bonjour.")]
        )
        == "Avec plaisir."
    )


def test_new_expression_is_not_a_decorative_suffix():
    assert (
        policy().finalize("Tea it is, *avec plaisir*.", "I prefer tea.") == "Tea it is."
    )
    assert policy().finalize("Avec plaisir.", "Thanks") == "Avec plaisir."


def test_generated_distress_greeting_is_removed_without_changing_guidance():
    assert (
        policy().finalize("Bonjour. Take one small step.", "I'm overwhelmed.")
        == "Take one small step."
    )


def test_distress_greeting_does_not_consume_the_practical_answer_budget():
    result = policy().finalize(
        "Bonjour. I hear you. Put one small task on paper. Leave the rest for later.",
        "I'm overwhelmed today. Give me one small practical step. No jokes.",
    )
    assert "Put one small task on paper." in result
    assert "Bonjour" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history,expected",
    [([], "Avec plaisir."), ([("assistant", "Bonjour.")], "Anytime.")],
)
async def test_active_routed_social_reply_receives_its_history(history, expected):
    from unittest.mock import AsyncMock, MagicMock, patch
    from agent.chat_workflow import ChatWorkflow
    from agent.kernel.feature_flags import PIPELINE_CONTROL_TOKEN
    from agent.orchestration.contracts import ResponseCandidate

    workflow = ChatWorkflow(persona=active_persona())
    workflow.routing_service.execute = AsyncMock(
        return_value=ResponseCandidate("Anytime.", "social:acknowledgement")
    )
    workflow.model_service.generate = AsyncMock(
        side_effect=AssertionError("No inference for a simple acknowledgement")
    )
    session = MagicMock()
    session.get_history.return_value = history
    with (
        patch("agent.chat_workflow.get_session_manager", return_value=session),
        patch("agent.chat_workflow.UserManager.update_user_profile"),
        patch("agent.chat_workflow.UserManager.get_user_profile", return_value={}),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record"),
        patch("agent.orchestration.turn_pipeline.turn_event_writer.record_pipeline"),
    ):
        result = await workflow.process_message(
            {
                "platform": "telegram",
                "external_user_id": "55",
                "external_chat_id": "300",
                "message_id": "french-social",
                "text": "Thanks",
                "internal_id": "owner",
                "_pipeline_mode": "active",
                "_pipeline_control_token": PIPELINE_CONTROL_TOKEN,
            }
        )
    assert result["text"] == expected
    workflow.model_service.generate.assert_not_awaited()
