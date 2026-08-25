from unittest.mock import patch

from llm import ensemble
from llm.ensemble import LocalTask, ask_ensemble, run_parallel, should_use_ensemble


def test_run_parallel_preserves_named_results():
    tasks = [LocalTask("one", "a"), LocalTask("two", "b", role="critic")]
    with patch.object(
        ensemble.manager, "ask_llm", side_effect=lambda prompt, **kw: prompt.upper()
    ):
        assert run_parallel(tasks) == {"one": "A", "two": "B"}


def test_empty_parallel_work_is_immediate():
    assert run_parallel([]) == {}


def test_ensemble_requires_explicit_request(monkeypatch):
    monkeypatch.setenv("LLM_ENSEMBLE_ENABLED", "true")
    assert should_use_ensemble("Use several agents to independently verify this")
    assert not should_use_ensemble("What is the capital of France?")


def test_ensemble_synthesizes_parallel_findings():
    with (
        patch.object(ensemble, "run_parallel", return_value={"reasoning": "fact"}),
        patch.object(ensemble.manager, "ask_llm", return_value="final") as ask,
    ):
        assert ensemble.ask_ensemble("question") == "final"
        assert "[reasoning]\nfact" in ask.call_args.args[0]


def test_selected_complex_reviews_use_ensemble_automatically(monkeypatch):
    monkeypatch.setenv("LLM_ENSEMBLE_ENABLED", "true")
    monkeypatch.setenv("LLM_ENSEMBLE_AUTO_COMPLEX", "true")
    monkeypatch.setenv("LLM_ENSEMBLE_EVAL_GAIN", "0.05")
    assert should_use_ensemble("Perform a security review of this design")
    assert not should_use_ensemble("How are you today?")


def test_automatic_ensemble_requires_measured_gain(monkeypatch):
    monkeypatch.setenv("LLM_ENSEMBLE_ENABLED", "true")
    monkeypatch.setenv("LLM_ENSEMBLE_AUTO_COMPLEX", "true")
    monkeypatch.delenv("LLM_ENSEMBLE_EVAL_GAIN", raising=False)
    assert not should_use_ensemble("Perform an architecture review of this design")


def test_high_stakes_requests_are_always_verified(monkeypatch):
    monkeypatch.setenv("LLM_ENSEMBLE_ENABLED", "true")
    monkeypatch.delenv("LLM_ENSEMBLE_EVAL_GAIN", raising=False)
    assert should_use_ensemble("Is this medication dosage safe?")
    assert should_use_ensemble("Assess this security vulnerability")


def test_response_cache_is_scoped_by_model():
    from llm.manager import ResponseCache

    key_a = ResponseCache._make_key("prompt", 0.2, 100, "model-a")
    key_b = ResponseCache._make_key("prompt", 0.2, 100, "model-b")
    assert key_a != key_b
