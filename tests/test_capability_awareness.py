import asyncio
from types import SimpleNamespace

from agent.orchestration.model_service import ModelConversationService
from agent.tooling import ToolRegistry, ToolResult, get_runtime_registry
from agent.tooling.registry import definition
from contracts import model_capability_manifest, model_capability_prompt
from llm.chat_prompt import ChatPrompt, conversation_prompt


class ManifestTool:
    def __init__(self, name: str, *, read_only: bool = True):
        self.name = name
        self.read_only = read_only

    async def execute(self, params, context):
        return ToolResult(text="done")


def test_manifest_is_exhaustive_and_includes_unavailable_and_internal_entries():
    available = definition(
        ManifestTool("available_tool"),
        description="Read a live value.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )
    unavailable = definition(
        ManifestTool("unavailable_tool", read_only=False),
        description="Perform a configured write.",
        availability_probe=lambda: (False, "Account is not configured"),
        required_permissions=("demo_write",),
        chat_routable=False,
    )
    registry = ToolRegistry((available, unavailable))

    manifest = model_capability_manifest(registry)
    prompt = model_capability_prompt(registry, query="Use available_tool query")

    assert manifest["total"] == 2
    assert manifest["available"] == 1
    assert manifest["unavailable"] == 1
    assert {item["name"] for item in manifest["capabilities"]} == {
        "available_tool",
        "unavailable_tool",
    }
    assert "- available_tool [available; chat_tool" in prompt
    assert "inputs=query" in prompt
    assert "unavailable_tool" in prompt

    unavailable_prompt = model_capability_prompt(
        registry, query="Why is unavailable_tool unavailable?"
    )
    assert "- unavailable_tool [unavailable; runtime_component" in unavailable_prompt
    assert "permissions=demo_write" in unavailable_prompt
    assert "Unavailable reason: Account is not configured" in unavailable_prompt


def test_live_index_lists_every_registered_capability_without_verbose_bloat():
    registry = get_runtime_registry()
    manifest = model_capability_manifest(registry)
    prompt = model_capability_prompt(registry)

    assert manifest["total"] == len(registry.names())
    assert {item["name"] for item in manifest["capabilities"]} == set(registry.names())
    assert all(name in prompt for name in registry.names())
    assert manifest["total"] > 40
    assert len(prompt) < 5000


def test_live_index_retrieves_only_request_relevant_verbose_details():
    prompt = model_capability_prompt(query="Show Atmosphere website page views")
    observatory = next(
        line
        for line in prompt.splitlines()
        if line.startswith("- observatory_project_analytics [")
    )

    assert "; chat_tool" in observatory
    assert "Read aggregate GA4 page views" in prompt
    assert "Available chat tools:" in prompt
    assert "- home_control [" not in prompt


def test_generic_model_review_terms_do_not_retrieve_unrelated_tool_details():
    prompt = model_capability_prompt(
        query="Use multiple models to review a bounded queue recommendation"
    )

    assert "Available chat tools:" in prompt
    assert "- home_alias_reject [" not in prompt
    assert "- dashboard [" not in prompt
    assert "- github_contribution_scan [" not in prompt


def test_runtime_manifest_covers_non_tool_workflow_functionality():
    names = set(get_runtime_registry().names())

    assert {
        "memory_service",
        "media_ingestion",
        "voice_delivery",
        "durable_tasks",
        "capability_discovery",
        "governance_controls",
        "adaptive_learning",
        "personal_organization",
    } <= names


def test_capability_context_is_a_system_message_for_chat_prompts(monkeypatch):
    monkeypatch.setattr(
        "contracts.model_capability_prompt",
        lambda **_kwargs: "[CURRENT RUNTIME CAPABILITY INDEX]\n- sentinel_tool [available]",
    )
    source = conversation_prompt("You are Curie.", [], "What can you do?")

    aware = ModelConversationService.capability_aware_prompt(source, "What can you do?")

    assert isinstance(aware, ChatPrompt)
    assert "sentinel_tool" in aware.messages[0]["content"]
    assert "sentinel_tool" not in aware.messages[-1]["content"]


def test_every_model_generation_receives_current_capability_context(monkeypatch):
    captured = []
    local = SimpleNamespace(
        DEFAULT_LLAMA_MODEL="general.gguf",
        ask_llm=lambda prompt, **_kwargs: captured.append(str(prompt)) or "Done.",
    )
    monkeypatch.setattr(
        "contracts.model_capability_prompt",
        lambda **_kwargs: "[CURRENT RUNTIME CAPABILITY INDEX]\n- always_present [available]",
    )
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _text: False)
    monkeypatch.setattr(
        "llm.providers.ask_best_provider", lambda *_args, **_kwargs: None
    )

    result = asyncio.run(
        ModelConversationService(local).generate(
            conversation_prompt("You are Curie.", [], "Hello"),
            "Hello",
            0.2,
        )
    )

    assert result.text
    assert captured
    assert all("always_present" in prompt for prompt in captured)
