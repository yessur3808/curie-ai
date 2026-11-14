from utils.calculator import calculate_request
import pytest


def test_calculator_accepts_natural_quick_prefix_and_unicode_multiplication():
    assert calculate_request("Quick one: what is 37 × 19?") == "37 × 19 = 703"


def test_calculator_accepts_spoken_operators():
    assert calculate_request("What is 84 divided by 7?") == "84 divided by 7 = 12"
    assert calculate_request("What is 12 times 4?") == "12 times 4 = 48"


def test_calculator_rejects_non_arithmetic_content():
    assert calculate_request("What is the meaning of life?") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Synthetic evidence: 18 of 20 trials succeeded. What percentage succeeded?",
            "90% succeeded (18 of 20).",
        ),
        ("2 out of 3 tests passed. What percent passed?", "≈66.67% passed (2 of 3)."),
        (
            "0 of 20 tasks completed. What percentage completed?",
            "0% completed (0 of 20).",
        ),
        (
            "20 of 20 attempts failed. What percentage failed?",
            "100% failed (20 of 20).",
        ),
    ],
)
def test_outcome_percentages_are_computed_not_generated(text, expected):
    assert calculate_request(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "18 of 20 trials succeeded. What percentage failed?",
        "18 of 0 trials succeeded. What percentage succeeded?",
        "21 of 20 trials succeeded. What percentage succeeded?",
        "18 of 20 trials succeeded. What percentage succeeded? Explain the causes.",
        "Did 18 of 20 trials succeed? What percentage succeeded?",
        "Not 18 of 20 trials succeeded. What percentage succeeded?",
    ],
)
def test_percentage_does_not_infer_other_outcomes_or_swallow_requests(text):
    assert calculate_request(text) is None
