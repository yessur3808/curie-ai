"""Draft streaming is internal; completion and cancellation are explicit."""

from collections import OrderedDict
from types import SimpleNamespace
import socket
import threading

import pytest

from llm import local_gateway as gateway


@pytest.fixture
def fake_model(monkeypatch):
    from llm import manager

    state = {"closed": False, "calls": 0, "pieces": ["Hello", " there."]}

    def completion(**kwargs):
        assert kwargs["stream"] is True
        state["calls"] += 1
        try:
            for piece in state["pieces"]:
                yield {"choices": [{"delta": {"content": piece}}]}
        finally:
            state["closed"] = True

    model = SimpleNamespace(tokenize=lambda _: [1], create_chat_completion=completion)
    monkeypatch.delenv("CURIE_INFERENCE_SOCKET", raising=False)
    monkeypatch.delenv("CURIE_INFERENCE_WORKER", raising=False)
    monkeypatch.setenv("CURIE_MODEL_SUPERVISOR", "off")
    monkeypatch.setenv("LLM_USE_CHAT_TEMPLATE", "true")
    monkeypatch.setattr(manager, "Llama", object())
    monkeypatch.setattr(
        manager, "llama_models_cache", OrderedDict([("fixture.gguf", model)])
    )
    monkeypatch.setattr(
        "llm.accelerators.should_use_npu", lambda _: pytest.fail("stream reached NPU")
    )
    return manager, state


def test_native_deltas_arrive_before_completion(fake_model):
    manager, state = fake_model
    seen = []

    def delta(text):
        assert not state["closed"]
        seen.append(text)

    result = manager.ask_llm("Say hello", model_name="fixture.gguf", on_delta=delta)
    assert seen == ["Hello", " there."]
    assert result == "Hello there."
    assert state["closed"]


def test_cancel_closes_native_generator_without_retry(fake_model):
    manager, state = fake_model

    def cancel(_):
        raise BrokenPipeError("consumer left")

    with pytest.raises(BrokenPipeError):
        manager.ask_llm("Say hello", model_name="fixture.gguf", on_delta=cancel)
    assert state["closed"] and state["calls"] == 1


def test_partial_draft_is_never_replaced_by_quality_retry(fake_model):
    manager, state = fake_model
    state["pieces"] = ["Here"]
    manager.ask_llm("Say hello", model_name="fixture.gguf", on_delta=lambda _: None)
    assert state["calls"] == 1


@pytest.mark.parametrize(
    "extra", [{"role": "critic"}, {"json_output": True}, {"stream": "yes"}]
)
def test_review_and_json_requests_cannot_opt_into_drafts(extra):
    with pytest.raises(ValueError, match="invalid_gateway_stream_request"):
        gateway.execute(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                **extra,
            },
            lambda *a, **k: pytest.fail("invalid request reached model"),
            lambda _: None,
        )


def test_legacy_execute_has_no_new_callback_argument():
    seen = {}
    gateway.execute(
        {"messages": [{"role": "user", "content": "hello"}]},
        lambda p, **kwargs: seen.update(kwargs) or "Hello",
    )
    assert "on_delta" not in seen


@pytest.fixture
def socket_server(tmp_path, monkeypatch):
    # A real private Unix socket verifies the wire protocol, not just mocks.
    path = str(tmp_path / "worker.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    import os

    os.chmod(path, 0o600)
    server.listen(1)
    server.settimeout(3)
    monkeypatch.setenv("CURIE_INFERENCE_SOCKET", path)
    monkeypatch.setenv("CURIE_INFERENCE_TIMEOUT_SECONDS", "3")
    threads = []
    errors = []

    def run(events, after=None):
        def serve():
            try:
                connection, _ = server.accept()
                with connection:
                    request = gateway.receive(connection)
                    assert request["stream"] is True
                    for event in events:
                        gateway.send(connection, event)
                    if after:
                        after(connection)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        threads.append(thread)
        thread.start()

    yield run
    server.close()
    for thread in threads:
        thread.join(4)
        assert not thread.is_alive()
    assert not errors


def test_transport_keeps_draft_separate_from_final(socket_server):
    socket_server(
        [
            {"type": "draft_delta", "text": "Unreviewed"},
            {"type": "done", "text": "Final cleaned answer"},
        ]
    )
    seen = []
    assert gateway.request_stream("hi", seen.append) == "Final cleaned answer"
    assert seen == ["Unreviewed"]


def test_transport_requires_explicit_completion(socket_server):
    socket_server([{"type": "draft_delta", "text": "Incomplete"}])
    with pytest.raises(ConnectionError):
        gateway.request_stream("hi", lambda _: None)


def test_callback_cancel_closes_transport(socket_server):
    closed = threading.Event()

    def after(connection):
        assert connection.recv(1) == b""
        closed.set()

    socket_server([{"type": "draft_delta", "text": "Hello"}], after)

    def cancel(_):
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        gateway.request_stream("hi", cancel)
    assert closed.wait(2)


@pytest.mark.parametrize(
    "event", [{"type": "error"}, {"type": "done"}, {"type": "draft_delta", "text": ""}]
)
def test_bad_stream_fails_closed(socket_server, event):
    socket_server([event])
    with pytest.raises((ValueError, RuntimeError)):
        gateway.request_stream("hi", lambda _: pytest.fail("invalid delta delivered"))
