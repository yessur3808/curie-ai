import asyncio
from types import SimpleNamespace

import pytest

from agent.orchestration.model_service import ModelConversationService
from llm.chat_prompt import conversation_prompt

OLD = "That last step sounds frustrating. What exact error message are you seeing, and when does it happen?"
NEW = "That sounds maddening. No troubleshooting, just sympathy."
USER = "No advice yet, just give me a little sympathy."


def prompt():
    return conversation_prompt(
        "Be helpful.", [("user", "I nearly fixed it."), ("assistant", OLD)], USER
    )


@pytest.mark.parametrize("retry", [NEW, OLD, None])
def test_substantial_repeated_answer_gets_at_most_one_retry(monkeypatch, retry):
    calls = []

    def ask(p, **kwargs):
        calls.append((p, kwargs))
        if retry is None:
            raise OSError("unavailable")
        return retry

    monkeypatch.setattr("llm.providers.ask_best_provider", lambda *a, **k: OLD)
    monkeypatch.setattr("llm.ensemble.should_use_ensemble", lambda _: False)
    service = ModelConversationService(
        SimpleNamespace(DEFAULT_LLAMA_MODEL="fixture", ask_llm=ask)
    )
    result = asyncio.run(service.generate(prompt(), USER, 0.3))
    assert len(calls) == 1
    assert calls[0][0].messages[-1]["content"] == USER
    assert "previous draft repeated" in calls[0][0].messages[0]["content"]
    assert result.text == (NEW if retry == NEW else OLD)
    assert result.metadata["coordination"]["model_calls"] == 2
    assert result.metadata["coordination"]["repeat_retry"] == (
        "replaced" if retry == NEW else "unresolved"
    )


@pytest.mark.parametrize(
    "user",
    [
        "Repeat that.",
        "Say that again.",
        "Quote your last reply verbatim.",
        "Rewrite that unchanged.",
    ],
)
def test_intentional_repetition_is_preserved(user):
    assert not ModelConversationService.repeats_previous_answer(prompt(), OLD, user)


def test_short_answers_code_and_evidence_are_not_regenerated():
    check = ModelConversationService.repeats_previous_answer
    assert not check(prompt(), "Yes.", USER)
    assert not check(prompt(), "```" + OLD + "```", USER)
    assert not check(prompt().with_evidence([{"id": "fixture"}]), OLD, USER)
