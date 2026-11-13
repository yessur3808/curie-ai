import pytest

from agent.orchestration.response_policy import enforce_line_and_tone_constraints


@pytest.mark.parametrize(
    "response,user,expected",
    [
        (
            "Got it. Your sailboat is named Osprey.",
            "Acknowledge in at most 5 words.",
            "Your sailboat is named Osprey.",
        ),
        (
            "Noted. Your sailboat is named Osprey.",
            "Acknowledge in at most 5 words.",
            "Your sailboat is named Osprey.",
        ),
        (
            "Understood. Your sailboat is named Osprey.",
            "Acknowledge in at most 5 words.",
            "Your sailboat is named Osprey.",
        ),
        (
            "The request failed; no cause was provided.",
            "Explain in at most 5 words.",
            "The request failed; no cause was provided.",
        ),
        (
            "Got it. Your sailboat is named Osprey.",
            "Acknowledge.",
            "Got it. Your sailboat is named Osprey.",
        ),
        ("Got it.", "Acknowledge in at most 1 words.", "Got it."),
    ],
)
def test_numeric_limit_removes_only_standalone_filler(response, user, expected):
    assert enforce_line_and_tone_constraints(response, user) == expected
