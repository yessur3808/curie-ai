from llm.chat_prompt import conversation_prompt
from agent.orchestration.model_service import ModelConversationService
from agent.response_planner import plan_response


def test_references_never_displace_latest_request_or_become_system_instructions():
    question = "You already shared this engineering update."
    prompt = conversation_prompt(
        "Be helpful.", [("assistant", "Earlier report.")], question
    )
    prompt = prompt.with_reference({"weather": "rain"}).with_reference(
        {"news": "old report"}
    )
    assert prompt.messages[-1] == {"role": "user", "content": question}
    assert prompt.messages[0] == {"role": "system", "content": "Be helpful."}
    assert str(prompt).endswith("User: " + question + "\nAssistant:")
    assert all(m["role"] == "user" for m in prompt.messages[2:-1])


def test_background_research_does_not_select_a_reasoning_role_for_thanks():
    prompt = conversation_prompt(
        "Be helpful.",
        [("user", "Tell me a joke."), ("assistant", "A joke.")],
        "Thanks for that.",
    )
    prompt = prompt.with_reference({"news": "research strategy and tradeoffs"})
    assert (
        ModelConversationService.contextual_role("Thanks for that.", prompt)
        == "general"
    )


def test_correction_is_not_classified_as_playful_banter():
    assert plan_response("That was not what I asked.")["interaction"] == "correction"


def test_repeated_brief_feedback_is_a_correction_across_turn_classifiers():
    from agent.kernel.dialogue_state import classify_transition, TransitionClass
    from agent.personality_adapter import PersonalityAdapter
    from agent.personality_context import PersonalityContext

    text = "Hey Curie, you already shared those agents with me."
    assert plan_response(text)["interaction"] == "correction"
    assert PersonalityAdapter().infer_context(text)["interaction_kind"] == "correction"
    assert classify_transition(text).kind is TransitionClass.CORRECTION
    policy = " ".join(
        PersonalityContext({"name": "Curie"}).build_compact_prompt_directives(text)
    )
    assert "do not resend the old item" in policy
    assert "saved preferences" in policy


def test_reference_insertion_preserves_machine_output_and_evidence():
    from llm.chat_prompt import ChatPrompt

    prompt = ChatPrompt(
        "task",
        [{"role": "user", "content": "Answer this request."}],
        evidence=[{"id": "fixture", "outcome": "completed"}],
        json_schema={"type": "object"},
    )
    result = prompt.with_reference({"background": "data"})
    assert result.evidence == prompt.evidence
    assert result.json_output and result.json_schema == prompt.json_schema
    assert result.messages[-1] == prompt.messages[-1]
