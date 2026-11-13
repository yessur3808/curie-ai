import pytest
from llm import manager, local_gateway
from llm import gateway_failover


@pytest.fixture(autouse=True)
def reset_failover(monkeypatch):
    gateway_failover.reset()
    monkeypatch.setattr(gateway_failover.time, "sleep", lambda _: None)
    monkeypatch.setenv("LLM_FAST_MODEL", "Qwen3-4B-Q4_K_M.gguf")
    yield
    gateway_failover.reset()


def test_crashed_primary_switches_to_fast_with_same_prompt(monkeypatch):
    monkeypatch.setenv("CURIE_INFERENCE_SOCKET", "/synthetic/socket")
    monkeypatch.delenv("CURIE_INFERENCE_WORKER", raising=False)
    monkeypatch.setenv("LLM_FAST_MODEL", "Qwen3-4B-Q4_K_M.gguf")
    calls = []
    prompt = object()

    def request(p, **kw):
        calls.append((p, kw))
        if kw["role"] != "fast":
            raise ConnectionResetError("synthetic worker crash")
        return "Recovered answer"

    monkeypatch.setattr(local_gateway, "request", request)
    assert (
        manager.ask_llm(prompt, role="general", max_tokens=256, owner_scope="synthetic")
        == "Recovered answer"
    )
    assert calls[-1][0] is prompt
    assert calls[-1][1]["model_name"] == "Qwen3-4B-Q4_K_M.gguf"
    assert calls[-1][1]["owner_scope"] == "synthetic"
    assert calls[-1][1]["max_tokens"] == 256


def test_cooldown_skips_primary_then_expires(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(gateway_failover.time, "monotonic", lambda: now[0])
    calls = []

    def request(prompt, **kw):
        calls.append(kw["role"])
        return "[Error: model failed]" if kw["role"] == "general" else "ok"

    monkeypatch.setattr(local_gateway, "request", request)
    for _ in range(2):
        assert gateway_failover.request_with_failover("test", role="general") == "ok"
    assert calls == ["general", "fast", "fast"]
    now[0] += 301
    gateway_failover.request_with_failover("test", role="general")
    assert calls[-2:] == ["general", "fast"]


def test_worker_restart_retries_only_recovery(monkeypatch):
    calls = []

    def request(prompt, **kw):
        calls.append(kw["role"])
        if len(calls) < 4:
            raise ConnectionRefusedError()
        return "ok"

    monkeypatch.setattr(local_gateway, "request", request)
    assert gateway_failover.request_with_failover("test", role="general") == "ok"
    assert calls == ["general", "fast", "fast", "fast"]


@pytest.mark.parametrize(
    "answer",
    ["I cannot help with that.", "Permission denied by the tool.", "A normal answer."],
)
def test_non_error_answers_do_not_switch_models(monkeypatch, answer):
    calls = []

    def request(prompt, **kw):
        calls.append(kw)
        return answer

    monkeypatch.setattr(local_gateway, "request", request)
    assert gateway_failover.request_with_failover("test", role="general") == answer
    assert len(calls) == 1


def test_all_models_down_is_bounded_and_honest(monkeypatch):
    calls = []

    def request(prompt, **kw):
        calls.append(kw)
        raise ConnectionResetError()

    monkeypatch.setattr(local_gateway, "request", request)
    assert gateway_failover.request_with_failover("test", role="general").startswith(
        "[Error:"
    )
    assert len(calls) == 6


def test_socket_permission_failure_is_not_retried(monkeypatch):
    calls = []

    def request(prompt, **kw):
        calls.append(kw)
        raise PermissionError()

    monkeypatch.setattr(local_gateway, "request", request)
    with pytest.raises(PermissionError):
        gateway_failover.request_with_failover("test", role="general")
    assert len(calls) == 1


def test_actual_workflow_recovers_without_tools(monkeypatch):
    import asyncio
    from agent.chat_workflow import ChatWorkflow
    from agent.tooling import get_runtime_registry

    monkeypatch.setenv("CURIE_INFERENCE_SOCKET", "/synthetic/socket")
    monkeypatch.delenv("CURIE_INFERENCE_WORKER", raising=False)
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setenv("LLM_ALTERNATE_ENABLED", "false")
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _: False)
    monkeypatch.setattr("llm.providers.ask_best_provider", manager.ask_llm)
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    calls = []

    def request(prompt, **kw):
        calls.append(kw["role"])
        if kw["role"] != "fast":
            raise ConnectionResetError()
        return "The fictional bird is named Wren."

    monkeypatch.setattr(local_gateway, "request", request)

    async def forbidden(*a, **kw):
        raise AssertionError("Conversational recovery must not execute tools")

    monkeypatch.setattr(get_runtime_registry(), "execute", forbidden)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    result = asyncio.run(
        workflow.process_message(
            {
                "platform": "api",
                "internal_id": "failover-synthetic",
                "external_user_id": "failover-synthetic",
                "external_chat_id": "failover-synthetic",
                "message_id": "1",
                "text": "Tell me the name of a fictional bird in a short sentence.",
            }
        )
    )
    assert "Wren" in result["text"]
    assert "general" in calls and "fast" in calls
