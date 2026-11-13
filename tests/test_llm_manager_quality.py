import pytest

from llm.manager import _response_quality_ok


@pytest.mark.parametrize(
    "response",
    ["Yes.", "No.", "Salut.", "Hello! Salut.", "Done.", "Anytime"],
)
def test_brief_complete_answers_pass_quality_check(response):
    assert _response_quality_ok(response)


@pytest.mark.parametrize(
    "response",
    [
        "",
        "   ",
        "...",
        "[Error: unavailable]",
        "I apologize.",
        "Here",
        "Here.",
        "Here are three ideas:",
    ],
)
def test_empty_error_or_apology_answers_fail_quality_check(response):
    assert not _response_quality_ok(response)


def test_incomplete_npu_response_falls_through_to_general_path(monkeypatch):
    from llm import accelerators, manager

    # This is an in-process fallback unit test. A developer's live shared-worker
    # socket must not turn it into an integration request.
    monkeypatch.delenv("CURIE_INFERENCE_SOCKET", raising=False)
    monkeypatch.setattr(accelerators, "should_use_npu", lambda prompt: True)
    monkeypatch.setattr(
        accelerators,
        "ask_npu",
        lambda prompt, temperature, max_tokens: "Here",
    )
    monkeypatch.setattr(manager, "Llama", None)

    assert manager.ask_llm("Give me three ideas", role="npu") == (
        "[Error: llama_cpp not installed]"
    )
