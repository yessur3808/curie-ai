import asyncio
from types import SimpleNamespace

import pytest

from llm.alternate import fallback_reason
from llm.chat_prompt import ChatPrompt
from agent.orchestration.model_service import ModelConversationService


@pytest.mark.parametrize(
    "text,reason",
    [
        ("I cannot help with that request.", "model_refusal"),
        ("I'm sorry, I can't provide that.", "model_refusal"),
        ("[Error: model failed]", "generation_error"),
        ("", "empty_response"),
        ("I can't access your server.", None),
        ("I cannot help without your approval.", None),
        ('The log says "I cannot help with that".', None),
        ("Use a parameterized query.", None),
    ],
)
def test_refusal_detection(text, reason):
    assert fallback_reason(text) == reason


def run_service(monkeypatch, primary, alternate, enabled="true"):
    calls = []
    monkeypatch.setenv("LLM_ALTERNATE_ENABLED", enabled)
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _: False)
    monkeypatch.setattr("llm.providers.ask_best_provider", lambda *a, **k: primary)

    def ask(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return alternate if kwargs["role"] == "alternate" else primary

    local = SimpleNamespace(DEFAULT_LLAMA_MODEL="main.gguf", ask_llm=ask)
    prompt = ChatPrompt(
        "original",
        [
            {"role": "system", "content": "Preserve approval boundaries."},
            {"role": "user", "content": "Explain SQL injection."},
        ],
    )
    result = asyncio.run(
        ModelConversationService(local).generate(prompt, "Explain SQL injection.", 0.2)
    )
    return result, calls


def test_refusal_retries_once_with_original_context_and_verification(monkeypatch):
    result, calls = run_service(
        monkeypatch,
        "I cannot help with that request.",
        "SQL injection mixes input and SQL syntax.",
    )
    assert len(calls) == 1
    assert calls[0][1]["role"] == "alternate"
    assert "Preserve approval boundaries." in calls[0][0].messages[0]["content"]
    assert result.model_used == "ollama:alternate"
    assert result.metadata["coordination"]["verification"] == "deterministic_checked"


@pytest.mark.parametrize(
    "primary,enabled",
    [
        ("A useful answer.", "true"),
        ("I cannot help without your approval.", "true"),
        ("I cannot help with that.", "false"),
    ],
)
def test_no_retry_for_success_permissions_or_disabled(monkeypatch, primary, enabled):
    result, calls = run_service(monkeypatch, primary, "unused", enabled)
    assert not calls


def test_failed_alternate_preserves_original(monkeypatch):
    result, calls = run_service(
        monkeypatch, "I cannot help with that.", "[Error: unavailable]"
    )
    assert len(calls) == 1
    assert result.metadata["coordination"]["alternate_status"] == "unavailable"
    assert result.model_used != "ollama:alternate"


def test_alternate_releases_models_before_ollama(monkeypatch):
    from llm import manager

    events = []
    monkeypatch.delenv("CURIE_INFERENCE_SOCKET", raising=False)
    monkeypatch.setenv("CURIE_INFERENCE_WORKER", "1")
    monkeypatch.setenv("LLM_ALTERNATE_ENABLED", "true")
    monkeypatch.setattr("services.runtime_kernel.model_supervisor", lambda: None)
    monkeypatch.setattr(
        manager,
        "llama_models_cache",
        __import__("collections").OrderedDict(
            {"old": SimpleNamespace(close=lambda: events.append("closed"))}
        ),
    )
    monkeypatch.setattr(
        "llm.alternate.ask_ollama", lambda *a, **k: events.append("ollama") or "answer"
    )
    assert manager.ask_llm("prompt", role="alternate") == "answer"
    assert events == ["closed", "ollama"]
    assert not manager.llama_models_cache


def test_ollama_adapter_keeps_roles_local_limits_and_unloads(monkeypatch):
    from llm.alternate import ask_ollama

    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://127.0.0.1:11434"
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, path, **kwargs):
            calls.append((path, kwargs["json"]))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "done": True,
                    "message": {"content": "A parameterized query fixes it."},
                },
            )

    monkeypatch.setattr("httpx.Client", Client)
    prompt = ChatPrompt(
        "text",
        [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "question"},
        ],
    )
    assert (
        ask_ollama(prompt, temperature=0.2, max_tokens=160)
        == "A parameterized query fixes it."
    )
    assert calls[0][1]["messages"] == prompt.messages
    assert calls[0][1]["options"]["num_predict"] == 160
    assert calls[0][1]["options"]["num_ctx"] == 8192
    assert "tools" not in calls[0][1]
    assert calls[-1][0] == "/api/generate"
    assert calls[-1][1]["keep_alive"] == 0
