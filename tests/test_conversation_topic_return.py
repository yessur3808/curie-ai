"""Explicit topic returns must retain in-window conversational evidence."""

import asyncio

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.orchestration.model_service import ResponseCandidate
from agent.tooling import get_runtime_registry


@pytest.mark.parametrize(
    "return_prompt",
    [
        "Back to our picnic: what was the budget and food restriction?",
        "Returning to the picnic, remind me of the budget and restriction.",
    ],
)
@pytest.mark.parametrize("extra_detours", [0, 4])
def test_topic_return_keeps_available_history(
    monkeypatch, return_prompt, extra_detours
):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    seen = []
    calls = []

    async def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Conversation must not execute tools")

    async def generate(prompt, user_text, *args, **kwargs):
        seen.append((user_text, prompt.messages))
        return ResponseCandidate("Understood.", "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    monkeypatch.setattr(workflow.model_service, "generate", generate)

    async def scenario():
        prompts = [
            "Our fictional picnic budget is 96 dollars. Mira cannot eat peanuts.",
            "Six of us will go to a riverside spot.",
            "Random topic: why do cats love cardboard boxes?",
            "Why do they sit in boxes that are too small?",
        ]
        prompts.extend(
            [
                "What makes a good fictional spaceship name?",
                "Call the fictional ship Kestrel.",
                "Why is the sky blue?",
                "Tea or coffee for a quiet evening?",
            ][:extra_detours]
        )
        prompts.append(return_prompt)
        for index, text in enumerate(prompts):
            result = await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "topic-return",
                    "external_user_id": "topic-return",
                    "external_chat_id": "topic-return",
                    "message_id": str(index),
                    "text": text,
                }
            )
        assert calls == []
        assert seen[-1][0] == return_prompt, result
        user_history = [m["content"] for m in seen[-1][1][:-1] if m["role"] == "user"]
        assert prompts[0] in user_history
        assert prompts[1] in user_history

    asyncio.run(scenario())


def test_topic_return_with_due_time_still_schedules():
    from agent.intent_router import classify_request

    result = classify_request(
        "Back to the picnic, remind me about packing in 30 minutes."
    )
    assert result.action == "reminder_create"
    assert result.needs_approval


def test_explicit_new_topic_still_discards_old_transcript():
    assert (
        ChatWorkflow._prompt_history_for_transition(
            [("user", "Old picnic facts")], None, "New topic: explain cats."
        )
        == []
    )


def test_topic_return_load_is_bounded_and_owner_scoped(monkeypatch):
    from unittest.mock import Mock

    manager = Mock()
    manager.get_history.return_value = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Entry {i}"}
        for i in range(60)
    ]
    monkeypatch.setattr("agent.chat_workflow.get_session_manager", lambda: manager)
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    workflow = ChatWorkflow()

    async def scenario():
        _, ordinary = await workflow._batch_load_context(
            "owner-a", "telegram", user_text="Hello"
        )
        _, returned = await workflow._batch_load_context(
            "owner-a", "telegram", user_text="Back to our plan."
        )
        assert len(ordinary) == 10
        assert len(returned) == 40
        assert returned[0][1] == "Entry 20"
        assert all(
            call.args == ("telegram", "owner-a")
            for call in manager.get_history.call_args_list
        )

    asyncio.run(scenario())


def test_real_device_alias_still_routes():
    from agent.intent_router import classify_request

    # Explicit delimiters establish the device/alias boundary without guessing.
    assert classify_request("Call the desk lamp Reading.").action == "clarify"
    result = classify_request('Call the desk lamp "Reading".')
    assert result.action == "home_alias"
    assert result.params == {"device": "desk lamp", "alias": "Reading"}


@pytest.mark.parametrize(
    "prompt",
    [
        "Call a fictional robot Pip.",
        "Please call an imaginary spaceship Kestrel.",
        "Call this pretend lamp Firefly.",
        "Please call our fictional rover Pebble.",
        "Name my imaginary spaceship Kestrel.",
        "Rename the fictional ship Kestrel.",
    ],
)
def test_fictional_names_never_execute_device_tools(monkeypatch, prompt):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    workflow = ChatWorkflow()
    workflow.learning_service.submit = lambda *a, **kw: None
    calls, generated = [], []

    async def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Fictional naming must never execute a device tool")

    async def generate(_prompt, user_text, *args, **kwargs):
        generated.append(user_text)
        return ResponseCandidate("That works as a fictional name.", "sandbox")

    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    monkeypatch.setattr(workflow.model_service, "generate", generate)
    asyncio.run(
        workflow.process_message(
            {
                "platform": "telegram",
                "internal_id": "fictional-naming",
                "external_user_id": "fictional-naming",
                "external_chat_id": "fictional-naming",
                "message_id": "1",
                "text": prompt,
            }
        )
    )
    assert calls == []
    assert generated == [prompt]


@pytest.mark.parametrize(
    "correction",
    [
        "Correction: the picnic budget is 96 dollars, not 80.",
        "Correction: our picnic budget is 96 dollars, not 80.",
    ],
)
@pytest.mark.parametrize("separator", [". The", " and the", "; the", ", and the"])
def test_summary_cannot_restore_corrected_budget(monkeypatch, correction, separator):
    monkeypatch.setenv("INTENT_LLM_ENABLED", "false")
    monkeypatch.setattr(
        "agent.chat_workflow.UserManager.get_user_profile", lambda *_: {}
    )
    monkeypatch.setattr("services.research_followup.needs_research", lambda *_: False)
    summary_prompts, generated, calls = [], [], []
    subject = "our picnic budget" if "our picnic" in correction else "the picnic budget"
    stale = subject.capitalize() + " is 80 dollars."

    def summarize(prompt, **kwargs):
        summary_prompts.append(str(prompt))
        return stale.rstrip(".") + separator + " location is riverside."

    async def generate(prompt, user_text, *args, **kwargs):
        generated.append((user_text, str(prompt), prompt.messages))
        return ResponseCandidate("Understood.", "sandbox")

    async def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Summary recall must not execute tools")

    monkeypatch.setattr("llm.providers.ask_best_provider", summarize)
    monkeypatch.setattr(get_runtime_registry(), "execute", blocked)
    workflow = ChatWorkflow()
    workflow._HISTORY_SUMMARISE_THRESHOLD = 4
    workflow._HISTORY_KEEP_RECENT = 2
    workflow.learning_service.submit = lambda *a, **kw: None
    monkeypatch.setattr(workflow.model_service, "generate", generate)

    async def scenario():
        for i, text in enumerate(
            [
                "Our picnic budget is 80 dollars, with a riverside location.",
                correction,
                "Different topic: why do cats enjoy boxes?",
                "Why do they like small ones?",
                "Back to the picnic: what budget did I correct it to?",
            ]
        ):
            await workflow.process_message(
                {
                    "platform": "telegram",
                    "internal_id": "summary-correction",
                    "external_user_id": "summary-correction",
                    "external_chat_id": "summary-correction",
                    "message_id": str(i),
                    "text": text,
                }
            )
        assert calls == []
        assert summary_prompts
        assert correction in generated[-1][1]
        assert stale not in generated[-1][1]
        assert "riverside" in generated[-1][1].lower()
        assert any(
            m["role"] == "assistant" and correction in m["content"]
            for m in generated[-1][2]
        )
        assert all(
            correction not in m["content"]
            for m in generated[-1][2]
            if m["role"] == "system"
        )

    asyncio.run(scenario())
