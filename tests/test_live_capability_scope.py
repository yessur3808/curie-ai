from agent.orchestration.model_service import ModelConversationService
from llm.chat_prompt import conversation_prompt


def test_conversation_scope_preserves_roles_and_avoids_false_tool_access():
    prompt = conversation_prompt("You are Curie.", [], "Switch on the lights")
    result = ModelConversationService.capability_aware_prompt(
        prompt, "Switch on the lights", scope="conversation_only"
    )
    assert result.messages[-1] == prompt.messages[-1]
    assert "cannot execute" in result.messages[0]["content"]
    assert len(result) - len(prompt) < 300


def test_user_words_do_not_select_conversation_scope():
    prompt = conversation_prompt("You are Curie.", [], "conversation_only")
    result = ModelConversationService.capability_aware_prompt(
        prompt, "conversation_only"
    )
    assert len(result) - len(prompt) > 300


def test_live_guidance_is_small_and_retains_correction_boundaries():
    from agent.personality_context import PersonalityContext
    from utils.persona import load_persona

    context = PersonalityContext(load_persona("curie.json"))
    guidance = " ".join(
        context.build_live_prompt_directives("Actually, I meant tomorrow, not today.")
    )
    assert "never invent" in guidance
    assert "corrections" in guidance
    assert len(guidance) < 900
    assert len(guidance) < len(
        " ".join(
            context.build_compact_prompt_directives(
                "Actually, I meant tomorrow, not today."
            )
        )
    )


def test_draft_transport_only_applies_to_local_conversation(monkeypatch):
    check = ModelConversationService.local_draft_stream_enabled
    monkeypatch.setenv("CURIE_LOCAL_DRAFT_STREAM", "true")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "llama.cpp")
    monkeypatch.setenv("LLM_GENERAL_PROVIDER", "local")
    assert check("conversation_only", "general")
    assert not check("full", "general")
    assert not check("conversation_only", "critic")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "qwen,llama.cpp")
    assert not check("conversation_only", "general")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "llama.cpp")
    monkeypatch.setenv("CURIE_LOCAL_DRAFT_STREAM", "false")
    assert not check("conversation_only", "general")


def test_raw_drafts_are_not_delivered_before_verification(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from llm.chat_prompt import conversation_prompt

    emitted = []
    reviewed = []

    class Managed:
        async def submit(self, operation, **kwargs):
            async def emit(text):
                assert reviewed
                emitted.append(text)

            await operation(emit, asyncio.Event())
            return SimpleNamespace(
                text=emitted[-1],
                output_tokens=3,
                total_ms=1,
                request_id="fixture",
                queue_ms=0,
                first_token_ms=1,
                tokens_per_second=1,
            )

    def ask(prompt, **kwargs):
        kwargs["on_delta"]("<think>private draft</think>Unreviewed answer")
        assert emitted == []
        return "Cleaned answer"

    def verify(prompt, answer, *args, **kwargs):
        reviewed.append(answer)
        return SimpleNamespace(
            answer="Reviewed answer", status="checked", issues=[], evidence_ids=[]
        )

    monkeypatch.setenv("CURIE_LOCAL_DRAFT_STREAM", "true")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "llama.cpp")
    monkeypatch.setenv("LLM_GENERAL_PROVIDER", "local")
    monkeypatch.setenv("LLM_ALTERNATE_ENABLED", "false")
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _: False)
    monkeypatch.setattr("llm.coordination.verify_answer", verify)
    monkeypatch.setattr(
        "llm.inference_service.get_inference_service", lambda: Managed()
    )
    result = asyncio.run(
        ModelConversationService(
            SimpleNamespace(ask_llm=ask, DEFAULT_LLAMA_MODEL="fixture")
        ).generate(
            conversation_prompt("Be concise", [], "How was your day?"),
            "How was your day?",
            0.3,
            capability_scope="conversation_only",
        )
    )
    assert emitted == ["Reviewed answer"]
    assert reviewed == ["Cleaned answer"]
    stage = result.metadata["coordination"]["stage_metrics"][0]
    assert stage["first_draft_ms"] >= 0
    assert stage["draft_characters"] > 0
    assert stage["delivery"] == "complete_answer"


def test_cancelled_draft_never_falls_back_or_emits(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pytest

    cancelled = asyncio.Event()
    calls = []

    class Managed:
        async def submit(self, operation, **kwargs):
            async def emit(_):
                pytest.fail("cancelled reply emitted")

            return await operation(emit, cancelled)

    def ask(prompt, **kwargs):
        calls.append(kwargs)
        cancelled.set()
        kwargs["on_delta"]("draft")
        pytest.fail("callback should stop generation")

    monkeypatch.setenv("CURIE_LOCAL_DRAFT_STREAM", "true")
    monkeypatch.setenv("LLM_PROVIDER_PRIORITY", "llama.cpp")
    monkeypatch.setenv("LLM_GENERAL_PROVIDER", "local")
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _: False)
    monkeypatch.setattr(
        "llm.inference_service.get_inference_service", lambda: Managed()
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ModelConversationService(
                SimpleNamespace(ask_llm=ask, DEFAULT_LLAMA_MODEL="fixture")
            ).generate(
                "hello", "How was your day?", 0.3, capability_scope="conversation_only"
            )
        )
    assert len(calls) == 1
