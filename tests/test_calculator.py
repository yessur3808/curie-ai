from utils.calculator import calculate_request


def test_calculator_accepts_natural_quick_prefix_and_unicode_multiplication():
    assert calculate_request("Quick one: what is 37 × 19?") == "37 × 19 = 703"


def test_calculator_accepts_spoken_operators():
    assert calculate_request("What is 84 divided by 7?") == "84 divided by 7 = 12"
    assert calculate_request("What is 12 times 4?") == "12 times 4 = 48"


def test_calculator_rejects_non_arithmetic_content():
    assert calculate_request("What is the meaning of life?") is None
