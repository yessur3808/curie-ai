from http.server import ThreadingHTTPServer
import json
import threading
import urllib.request

import pytest

from scripts.ai_scout_model_bridge import make_handler, ranking_messages


def test_bridge_rejects_tools_other_models_and_oversized_requests():
    valid = {
        "model": "curie-ai-scout",
        "messages": [{"role": "user", "content": "rank evidence"}],
    }
    assert ranking_messages(valid)[0]["content"] == "rank evidence"
    for changes in (
        {"model": "other"},
        {"tools": [{"type": "function"}]},
        {"messages": [{"role": "user", "content": "x" * 24001}]},
        {"messages": [{"role": "tool", "content": "unsupported"}]},
    ):
        with pytest.raises(ValueError):
            ranking_messages({**valid, **changes})


def test_bridge_requires_auth_and_supports_streamed_rankings():
    calls = []

    def ask(messages):
        calls.append(messages)
        return '{"selected_ids":["one"]}'

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("fixture-key", ask))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(url + "/v1/models")
        assert exc.value.code == 401 and not calls
        req = urllib.request.Request(
            url + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "curie-ai-scout",
                    "messages": [{"role": "user", "content": "rank"}],
                    "stream": True,
                }
            ).encode(),
            headers={
                "Authorization": "Bearer fixture-key",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req) as response:
            body = response.read().decode()
        assert "data: [DONE]" in body and "chat.completion.chunk" in body
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_shared_model_uses_real_gateway_prompt(monkeypatch):
    from scripts.ai_scout_model_bridge import ask_shared_model
    from llm.chat_prompt import ChatPrompt
    import llm.local_gateway as gateway

    def request(prompt, **options):
        assert isinstance(prompt, ChatPrompt)
        assert prompt.json_output is True
        assert prompt.messages == [{"role": "user", "content": "Rank this packet"}]
        assert options["role"] == "fast"
        assert options["max_tokens"] == 128
        return '{"selected_ids":["one"]}'

    monkeypatch.setattr(gateway, "request", request)
    assert (
        ask_shared_model([{"role": "user", "content": "Rank this packet"}])
        == '{"selected_ids":["one"]}'
    )


def test_specialist_alias_uses_existing_fast_worker(monkeypatch):
    from scripts.ai_scout_model_bridge import ask_shared_specialist
    import llm.local_gateway as gateway

    observed = []
    monkeypatch.setattr(
        gateway,
        "request",
        lambda prompt, **kw: observed.append((prompt, kw)) or "Assessment",
    )
    assert (
        ask_shared_specialist(
            [
                {"role": "system", "content": "Unused coding harness"},
                {"role": "user", "content": "Review supplied evidence"},
            ]
        )
        == "Assessment"
    )
    assert observed[0][1]["role"] == "fast"
    assert observed[0][1]["max_tokens"] == 256
    assert "Unused coding harness" not in str(observed[0][0])
    assert "Review supplied evidence" in str(observed[0][0])
    assert ranking_messages(
        {"model": "curie-specialist", "messages": [{"role": "user", "content": "task"}]}
    )
