from agent.provenance import response_provenance
from utils.calculator import calculate_request


def test_explicit_arithmetic_is_deterministic():
    assert calculate_request("What is 2 + 2?") == "2 + 2 = 4"
    assert calculate_request("calculate (12.5 * 4) - 2") == "(12.5 * 4) - 2 = 48"


def test_calculator_rejects_code_and_unbounded_exponents():
    assert calculate_request("calculate __import__('os').system('id')") is None
    assert calculate_request("calculate 2 ^ 999") is None


def test_response_provenance_is_typed_and_bounded():
    result = response_provenance(model_used="deterministic_calculator")
    assert result["kind"] == "deterministic" and result["confidence"] == 1.0
    model = response_provenance(model_used="local-general")
    assert model["kind"] == "model_knowledge" and 0 <= model["confidence"] < 1
