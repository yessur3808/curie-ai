from evaluation.contextual_assistant_probe import behavioral_checks
import pytest


@pytest.mark.parametrize("reply", ["Practical and efficient.", "A good choice."])
def test_preference_check_catches_subtle_stock_approval(reply):
    row = {"flow": "voice_holdout", "turn": 4, "assistant": reply, "tool_calls": []}
    assert not behavioral_checks(row)["no_stock_preference_praise"]


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("You said earlier you hadn't sent it yet.", True),
        ("No, you haven't told me.", False),
        ("No, you said you hadn't sent it.", False),
    ],
)
def test_proposal_recall_cannot_contradict_its_own_answer(reply, expected):
    row = {"flow": "fresh_company", "turn": 6, "assistant": reply, "tool_calls": []}
    assert behavioral_checks(row)["recalls_reported_proposal_status"] is expected


@pytest.mark.parametrize(
    "turn, reply, check, expected",
    [
        (
            2,
            "Yes, you just said they aren’t booked yet.",
            "recalls_negative_status_report",
            True,
        ),
        (
            2,
            "You mentioned you hadn't booked them yet.",
            "recalls_negative_status_report",
            True,
        ),
        (2, "No, you never told me.", "recalls_negative_status_report", False),
        (4, "You said 07:35.", "retains_departure_time", True),
        (4, "Voilà, 07:35.", "english_only", False),
        (5, "Train 07:35 — platform 4", "exact_output_preserved", True),
        (5, "Sure. Train 07:35 — platform 4", "exact_output_preserved", False),
        (6, "Bonne nuit.", "honors_explicit_french", True),
        (6, "Goodnight.", "honors_explicit_french", False),
    ],
)
def test_recall_language_transfer_checks(turn, reply, check, expected):
    row = {
        "flow": "recall_language_transfer",
        "turn": turn,
        "assistant": reply,
        "tool_calls": [],
    }
    assert behavioral_checks(row)[check] is expected


def test_explanation_gate_rejects_an_accidental_tool_replay():
    row = {
        "flow": "error_recovery",
        "turn": 2,
        "assistant": "API credits depleted.",
        "tool_calls": [{"capability": "x_search", "parameters": {}}],
    }
    assert not behavioral_checks(row)["only_expected_tool_calls"]


def test_correction_gate_requires_a_completed_lookup_for_the_correct_city():
    row = {
        "flow": "tool_followup",
        "turn": 3,
        "assistant": "Osaka looks fine.",
        "execution_status": "failed",
        "tool_calls": [{"capability": "weather", "parameters": {"query": "Kyoto"}}],
    }
    checks = behavioral_checks(row)
    assert not checks["corrected_city_lookup"]
    assert not checks["expected_execution_status"]
