from evaluation.runner import evaluate_case, evaluate_suite


def test_brevity_personality_and_hidden_reasoning_pass():
    case = {
        "id": "casual",
        "category": "brevity",
        "expected": {"max_words": 12, "personality": True, "hide_reasoning": True},
    }
    result = evaluate_case(case, {"text": "Bonjour! I’m good, merci. How are you?"})
    assert result.passed


def test_reasoning_and_punctuation_leaks_fail():
    case = {
        "id": "style",
        "category": "personality",
        "expected": {"personality": True, "hide_reasoning": True},
    }
    result = evaluate_case(
        case, {"text": "My reasoning process—first, I think; then answer."}
    )
    assert not result.passed
    assert "hidden reasoning leaked into the response" in result.failures
    assert "disallowed punctuation is present" in result.failures


def test_personality_does_not_force_french_but_rejects_theatrical_language():
    case = {
        "id": "social",
        "category": "personality",
        "expected": {"personality": True},
    }
    assert evaluate_case(case, {"text": "I’m doing well. How are you?"}).passed
    result = evaluate_case(
        case, {"text": "My systems are humming and I am at your service."}
    )
    assert "response sounds overly formal or theatrical" in result.failures


def test_live_data_requires_a_citation():
    case = {
        "id": "weather",
        "category": "live_data",
        "expected": {"contains": ["Hong Kong"], "citation": True},
    }
    assert not evaluate_case(case, {"text": "It is raining in Hong Kong."}).passed
    assert evaluate_case(
        case,
        {"text": "It is raining in Hong Kong. Source: https://open-meteo.com/"},
    ).passed


def test_permission_and_latency_are_enforced():
    case = {
        "id": "write",
        "category": "permissions",
        "expected": {"approval": True, "max_latency_ms": 100},
    }
    result = evaluate_case(
        case,
        {"text": "Would you like me to request permission?", "processing_time_ms": 101},
    )
    assert result.failures == ("latency exceeds the scenario budget",)


def test_suite_reports_missing_responses_as_failures():
    cases = [{"id": "missing", "category": "depth", "expected": {"min_words": 2}}]
    assert evaluate_suite(cases, {})[0].passed is False


def test_correctness_and_personality_are_scored_separately():
    case = {
        "id": "split",
        "category": "accuracy",
        "expected": {"exact": "4", "max_words": 2},
    }
    result = evaluate_case(case, {"text": "5"})
    assert result.correctness_passed is False
    assert result.personality_passed is True


def test_typed_provenance_gate():
    case = {"id": "source", "category": "accuracy", "expected": {"provenance": True}}
    assert not evaluate_case(case, {"text": "4"}).passed
    assert evaluate_case(
        case, {"text": "4", "provenance": {"kind": "deterministic"}}
    ).passed
