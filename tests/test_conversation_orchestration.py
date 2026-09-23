import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.orchestration.learning_service import ConversationLearningService
from agent.orchestration.model_service import ModelConversationService
from agent.orchestration.response_policy import ResponsePolicy
from agent.orchestration.session_commands import SessionCommandService
from agent.orchestration.specialist_router import SpecialistRouter

pytestmark = pytest.mark.integration


class SessionStore:
    def __init__(self):
        self.reset = []

    def reset_session(self, platform, user):
        self.reset.append((platform, user))

    def get_history(self, platform, user):
        return [{"role": "user", "content": "hello"}]


def test_session_commands_are_isolated_and_token_free():
    store = SessionStore()
    service = SessionCommandService(lambda: store)
    result = service.handle(" /RESET ", "telegram", "u1")
    assert result.model_used == "system"
    assert store.reset == [("telegram", "u1")]
    assert service.handle("hello", "telegram", "u1") is None


def test_specialist_selection_is_explicit_and_priority_ordered():
    router = SpecialistRouter()
    assert router.select("Remind me to review my Python code") == "scheduler_skill"
    assert router.select("Show traffic directions home") == "navigation_skill"
    assert router.select("Tell me a joke") is None


def test_specialist_router_invokes_only_selected_handler(monkeypatch):
    router = SpecialistRouter()
    scheduler = AsyncMock(return_value="Reminder saved")
    coding = AsyncMock(return_value="Code answer")
    monkeypatch.setattr(router, "_scheduler", scheduler)
    monkeypatch.setattr(router, "_coding", coding)
    result = asyncio.run(router.handle("Remind me tomorrow", "u1", "telegram"))
    assert result.text == "Reminder saved"
    scheduler.assert_awaited_once()
    coding.assert_not_awaited()


def test_response_policy_is_single_sanitize_and_personality_boundary():
    class Personality:
        def apply_response_style(
            self, response, user_text, user_profile=None, history=None
        ):
            return response + " styled"

    policy = ResponsePolicy({"name": "Curie"}, Personality(), minimal_sanitization=True)
    result = policy.finalize(
        "Assistant: <think>secret</think>I can help — oui.", "help"
    )
    assert "secret" not in result
    assert "Assistant:" not in result
    assert "—" not in result
    assert result.endswith("styled")


def test_response_policy_removes_decorative_french_suffix():
    class Personality:
        def apply_response_style(
            self, response, user_text, user_profile=None, history=None
        ):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Personality())
    assert (
        policy.finalize(
            "Understood. The lights can stay off, *ça va*.",
            "I don't need the lights on",
        )
        == "Understood. The lights can stay off."
    )


def test_response_policy_preserves_punctuation_when_removing_formal_address():
    class Personality:
        def apply_response_style(
            self, response, user_text, user_profile=None, history=None
        ):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Personality())

    assert (
        policy.finalize(
            "The AI Sync Box strip is already off, monsieur.",
            "Turn off the TV light",
        )
        == "The AI Sync Box strip is already off."
    )


def test_response_policy_removes_unasked_follow_up_when_user_requests_briefness():
    class Personality:
        def apply_response_style(
            self, response, user_text, user_profile=None, history=None
        ):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Personality())

    assert (
        policy.finalize(
            "- Tea\n- Music\n- Read\n\nWhich one sounds easiest tonight?",
            "Give me three ideas. Keep it brief.",
        )
        == "- Tea\n- Music\n- Read"
    )


def test_brief_follow_up_cleanup_does_not_truncate_here_are_answer():
    class Personality:
        def apply_response_style(
            self, response, user_text, user_profile=None, history=None
        ):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Personality())
    response = (
        "Here are three low-effort ways to unwind: * **Change the air**: "
        "Open a window. * **Warm drink**: Make tea. * **Easy reading**: "
        "Flip through a familiar book. Which one sounds easiest tonight?"
    )

    assert policy.finalize(
        response,
        "Give me three low-effort ways to unwind tonight. Keep it brief.",
    ) == (
        "Here are three low-effort ways to unwind: * **Change the air**: "
        "Open a window. * **Warm drink**: Make tea. * **Easy reading**: "
        "Flip through a familiar book."
    )


def test_response_policy_removes_forced_italicized_french_aside():
    class Personality:
        def apply_response_style(
            self, response, user_text, user_profile=None, history=None
        ):
            return response

    policy = ResponsePolicy({"name": "Curie"}, Personality())

    assert (
        policy.finalize(
            "You asked how I was doing, keeping it casual and short. *Oui, that is the spirit.*",
            "What did I just ask?",
        )
        == "You asked how I was doing, keeping it casual and short."
    )


def test_model_service_uses_local_fallback(monkeypatch):
    local = SimpleNamespace(
        DEFAULT_LLAMA_MODEL="local-model",
        ask_llm=lambda *args, **kwargs: "Local answer",
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "llm.ensemble",
        SimpleNamespace(
            should_use_ensemble=lambda text: False, ask_ensemble=lambda prompt: None
        ),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "llm.providers",
        SimpleNamespace(
            ask_best_provider=lambda *args, **kwargs: "[Error unavailable]"
        ),
    )
    result = asyncio.run(
        ModelConversationService(local).generate("prompt", "hello", 0.4)
    )
    assert result.text == "Local answer"
    assert result.model_used == "local-model:general"


def test_learning_service_submits_to_bounded_executor():
    calls = []

    class Executor:
        def submit(self, fn, *args):
            calls.append((fn, args))

    def learner(*args):
        return None

    ConversationLearningService(Executor(), learner=learner).submit(
        "u1", "hello", "bonjour"
    )
    assert calls[0][1] == ("u1", "hello", "bonjour")
