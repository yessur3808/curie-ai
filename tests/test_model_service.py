from agent.orchestration.model_service import ModelConversationService
import asyncio
from types import SimpleNamespace


def test_casual_turns_use_a_short_generation_budget():
    assert ModelConversationService.token_budget("Hi Curie, how are you?") == 96
    assert ModelConversationService.token_budget("Bonjour!") == 96


def test_explicit_depth_keeps_a_large_generation_budget():
    assert ModelConversationService.token_budget("Explain this in detail") == 1024
    assert ModelConversationService.token_budget("Give me a thorough comparison") == 1024


def test_normal_turns_use_a_balanced_generation_budget():
    assert ModelConversationService.token_budget("What causes rain?") == 384


def test_task_content_selects_specialized_model_roles():
    assert ModelConversationService.model_role("Fix the authentication bug and run pytest") == "coding"
    assert ModelConversationService.model_role("Analyze these market tradeoffs") == "reasoning"
    assert ModelConversationService.model_role("Summarize this document") == "fast"
    assert ModelConversationService.model_role("How was your day?") == "general"


def test_local_fallback_receives_selected_role(monkeypatch):
    calls = []
    local = SimpleNamespace(
        DEFAULT_LLAMA_MODEL="general.gguf",
        ask_llm=lambda *args, **kwargs: calls.append(kwargs) or "done",
    )
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _text: False)
    monkeypatch.setattr("llm.providers.ask_best_provider", lambda *args, **kwargs: None)

    result = asyncio.run(
        ModelConversationService(local).generate("prompt", "debug this Python code", 0.2)
    )

    assert calls[-1]["role"] == "coding"
    assert result.model_used == "general.gguf:coding"
