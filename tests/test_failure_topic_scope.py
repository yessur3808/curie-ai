from dataclasses import replace

import pytest

from agent.chat_workflow import ChatWorkflow
from agent.kernel.context import ContextSection
from agent.kernel.dialogue_state import (
    DialogueState,
    DialogueTransition,
    StateValue,
    TransitionClass,
)
from agent.kernel.understanding import analyze_turn


def slot(value, created_at, turn):
    return StateValue(value, turn, created_at, None, 1.0, "owner")


def state():
    return DialogueState(
        last_failed_goal=slot(
            {"response": "X returned HTTP 402: API credits depleted."},
            100,
            "failed-search",
        ),
        active_topic=slot("dinner", 200, "new-topic"),
    )


@pytest.mark.parametrize(
    "text",
    [
        "Ask me one question about dinner.",
        "Why that choice?",
        "I prefer noodles.",
        "Goodnight.",
        "What do you like about it?",
    ],
)
def test_new_subject_does_not_inherit_old_error(text):
    assert not ChatWorkflow._failure_context_relevant(state(), text)


@pytest.mark.parametrize(
    "text",
    [
        "Back to the search: why did it fail?",
        "What was the error?",
        "You mentioned an error earlier. Explain it.",
    ],
)
def test_explicit_error_recall_keeps_historical_evidence(text):
    assert ChatWorkflow._failure_context_relevant(state(), text)


def test_continuing_original_failure_keeps_evidence():
    snapshot = replace(state(), active_topic=slot("search", 100, "failed-search"))
    assert ChatWorkflow._failure_context_relevant(snapshot, "What can we do instead?")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ask me one question about dinner.", False),
        ("Back to the search: why did it fail?", True),
    ],
)
def test_actual_context_envelope_scopes_failure_evidence(monkeypatch, text, expected):
    workflow = ChatWorkflow(persona={"name": "Curie"})
    snapshot = state()
    monkeypatch.setattr(workflow.dialogue_state, "snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(
        workflow.context_budgeter, "memory_retrieval_allowed", lambda *a, **k: False
    )
    analysis = analyze_turn(text, text, owner_id="owner", platform="telegram")
    envelope, *_ = workflow._prepare_prompt_context(
        [],
        text,
        internal_id="owner",
        platform="telegram",
        user_profile={},
        turn_analysis=analysis,
        transition=DialogueTransition(
            TransitionClass.CONTINUATION, "test continuation"
        ),
    )
    assert bool(envelope.included(ContextSection.TOOL_RESULT)) is expected
    # Scoping the prompt must not erase recorded evidence.
    assert snapshot.last_failed_goal.value["response"].startswith("X returned HTTP 402")
