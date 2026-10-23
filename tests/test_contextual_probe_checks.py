from evaluation.contextual_assistant_probe import behavioral_checks


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
