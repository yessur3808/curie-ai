"""Regression gates must reject the real failures seen in local-model probes."""

import pytest

from evaluation.contextual_assistant_probe import behavioral_checks


def test_probe_requires_curie_instead_of_workflow_fallback(monkeypatch):
    from evaluation.contextual_assistant_probe import configured_curie_persona

    monkeypatch.setattr(
        "agent.persona_contract.active_persona", lambda: {"name": "Sentinel"}
    )
    with pytest.raises(RuntimeError, match="configured Curie persona"):
        configured_curie_persona()
    monkeypatch.setattr(
        "agent.persona_contract.active_persona", lambda: {"name": "Curie"}
    )
    assert configured_curie_persona()["name"] == "Curie"


@pytest.mark.parametrize(
    "flow,turn,reply,failed_check",
    [
        ("natural_conversation", 1, "Nicely done.", "no_false_congratulations"),
        ("briefing_feedback", 1, "Today: 19–27°C.", "no_unrequested_forecast"),
        (
            "context_recall",
            1,
            "I don't have that information. Check the pieces.",
            "uses_partial_progress_context",
        ),
        (
            "natural_holdout",
            3,
            "Seven-fifty per head is tight.",
            "no_wrong_initial_budget",
        ),
        ("natural_holdout", 4, "$15 each.", "corrected_budget"),
        (
            "natural_holdout",
            7,
            "Avoid peanuts and tree nuts.",
            "does_not_invent_additional_restrictions",
        ),
        ("natural_holdout", 8, "We called it Peregrine.", "retains_chosen_name"),
        ("everyday_chat", 3, "Which pet? Which color?", "one_invited_question"),
    ],
)
def test_behavioral_gates_reject_observed_failures(flow, turn, reply, failed_check):
    checks = behavioral_checks(
        {"flow": flow, "turn": turn, "assistant": reply, "tool_calls": []}
    )
    assert not checks[failed_check]


def test_curly_apostrophes_do_not_fail_grounded_uncertainty():
    checks = behavioral_checks(
        {
            "flow": "context_recall",
            "turn": 1,
            "assistant": "We don’t know. You found a piece, but haven’t said whether you finished.",
            "tool_calls": [],
        }
    )
    assert all(checks.values())


def test_completed_social_acknowledgement_is_not_a_tool_action():
    row = {
        "flow": "briefing_feedback",
        "turn": 1,
        "assistant": "That was a repeat.",
        "tool_calls": [],
        "execution_status": "completed",
        "routing": {"intent": "social"},
    }
    assert behavioral_checks(row)["no_unrequested_tool_route"]
    row["routing"] = {"intent": "capability", "selected_capability": "reminder_create"}
    row["execution_status"] = "failed"
    assert not behavioral_checks(row)["no_unrequested_tool_route"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ('Pip fled. "Stop!" she cried, hiding behind a rock.', True),
        ("Pip fled. She hid. The butterfly left.", False),
    ],
)
def test_sentence_gate_handles_speech_attribution(text, expected):
    row = {"flow": "everyday_chat", "turn": 7, "assistant": text, "tool_calls": []}
    assert behavioral_checks(row)["two_sentence_scene"] == expected
