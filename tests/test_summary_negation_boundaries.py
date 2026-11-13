"""Ordinary negative constraints are not explicit corrections."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.kernel.summary import extract_explicit_corrections
from agent.orchestration.model_service import ResponseCandidate
from agent.tooling import get_runtime_registry


@pytest.mark.parametrize(
    "constraint",
    [
        "No snacks may contain walnuts.",
        "Please use no sesame in the snacks.",
        "There is no need to book anything.",
    ],
)
def test_negative_constraints_do_not_become_corrections(monkeypatch, constraint):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    seen, calls = [], []

    async def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Conversation must not execute tools")

    async def generate(prompt, user_text, *args, **kwargs):
        seen.append(str(prompt))
        return ResponseCandidate("Understood.", "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    monkeypatch.setattr(
        "llm.providers.ask_best_provider",
        lambda *a, **kw: "The outing has eight guests. " + constraint,
    )
    workflow = ChatWorkflow()
    workflow._HISTORY_SUMMARISE_THRESHOLD = 4
    workflow._HISTORY_KEEP_RECENT = 2
    workflow.learning_service.submit = lambda *a, **kw: None
    monkeypatch.setattr(workflow.model_service, "generate", generate)

    async def scenario():
        for i, prompt in enumerate(
            [
                "Our fictional outing has eight guests. " + constraint,
                "Different topic: why does paper float?",
                "Back to the outing: repeat the food or booking constraint.",
                "Back to the outing: how many guests are coming?",
            ]
        ):
            await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "negative-summary",
                    "external_user_id": "negative-summary",
                    "external_chat_id": "negative-summary",
                    "message_id": str(i),
                    "text": prompt,
                }
            )
        assert calls == []
        assert "Working-context summary" in seen[-1]
        assert "Explicit correction:" not in seen[-1]
        assert constraint in seen[-1]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "text,target",
    [
        ("No, I meant Wren.", "Wren"),
        ("That's wrong, Wren.", "Wren"),
        ("There is no device called Aurora.", "Aurora"),
    ],
)
def test_explicit_correction_controls(text, target):
    found = extract_explicit_corrections([("user", text)], str)
    assert len(found) == 1
    assert found[0].target == target
