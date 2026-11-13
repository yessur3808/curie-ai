import json

import pytest

from agent.understanding.classifier import _prompt, relevant_taxonomy_subset
from llm.chat_prompt import ChatPrompt
from llm.local_gateway import execute


def prompt():
    text = "Inspect RAM on this host"
    return _prompt(
        text,
        relevant_taxonomy_subset(text),
        {"ram_usage": "Read local RAM"},
        {"ram_usage": {"required": [], "optional": [], "properties": {}}},
    )


def test_schema_accepts_only_valid_tool_arguments_and_compiles():
    LlamaGrammar = pytest.importorskip("llama_cpp").LlamaGrammar

    schema = prompt().json_schema
    variants = schema["oneOf"]
    assert len(variants) == 2
    tool = variants[0]["properties"]
    assert tool["intent"] == {"enum": ["local_system_inspection"]}
    assert tool["candidate_capability"] == {"enum": ["ram_usage"]}
    assert tool["entities"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert variants[1]["properties"]["candidate_capability"] == {"enum": [None]}
    assert LlamaGrammar.from_json_schema(json.dumps(schema), verbose=False) is not None


def test_schema_survives_prompt_helpers_and_gateway():
    original = prompt()
    for item in [
        original,
        original.with_evidence([]),
        original.with_json_output(),
        original.with_system_guidance("Keep validation"),
        original.with_reference({"note": "untrusted"}),
    ]:
        assert item.json_schema == original.json_schema
        seen = []
        execute(
            {
                "messages": item.messages,
                "json_output": True,
                "json_schema": item.json_schema,
            },
            lambda p, **kwargs: seen.append(p) or "{}",
        )
        assert seen[0].json_schema == original.json_schema


@pytest.mark.parametrize("schema,json_output", [("bad", True), ([], True), ({}, False)])
def test_gateway_rejects_invalid_schema_envelope(schema, json_output):
    with pytest.raises(ValueError, match="invalid_gateway_json_schema"):
        execute(
            {
                "messages": [{"role": "user", "content": "test"}],
                "json_output": json_output,
                "json_schema": schema,
            },
            lambda *args, **kwargs: pytest.fail("invalid payload reached inference"),
        )


def test_schema_reaches_llama_backend_and_bypasses_unconstrained_npu(monkeypatch):
    from collections import OrderedDict
    from types import SimpleNamespace
    from llm import manager

    observed = []

    def completion(**kwargs):
        observed.append(kwargs)
        return {"choices": [{"message": {"content": "{}"}}]}

    fixture = SimpleNamespace(tokenize=lambda _: [1], create_chat_completion=completion)
    monkeypatch.delenv("CURIE_INFERENCE_SOCKET", raising=False)
    monkeypatch.delenv("CURIE_INFERENCE_WORKER", raising=False)
    monkeypatch.setenv("CURIE_MODEL_SUPERVISOR", "off")
    monkeypatch.setenv("LLM_USE_CHAT_TEMPLATE", "true")
    monkeypatch.setattr(
        "llm.accelerators.should_use_npu",
        lambda _: pytest.fail("Schema request reached unconstrained NPU"),
    )
    monkeypatch.setattr(manager, "Llama", object())
    monkeypatch.setattr(
        manager, "llama_models_cache", OrderedDict([("fixture.gguf", fixture)])
    )
    request = prompt()
    manager.ask_llm(request, model_name="fixture.gguf")
    assert observed[0]["response_format"] == {
        "type": "json_object",
        "schema": request.json_schema,
    }
