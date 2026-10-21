import json

import pytest

from evaluation.conversation_template_runner import (
    TemplateValidationError,
    discover_templates,
    evaluate_check,
    load_template,
    run_template,
    validate_template,
)


class _FakeClient:
    def __init__(self, replies, *, fail_at=None):
        self.replies = list(replies)
        self.fail_at = fail_at
        self.chat_calls = []
        self.closed = []

    def open_session(self, user_id):
        self.user_id = user_id
        return "native-session"

    def chat(self, payload):
        self.chat_calls.append(payload)
        if self.fail_at == len(self.chat_calls):
            raise RuntimeError("simulated failure")
        return {
            "session_id": "native-session",
            "text": self.replies[len(self.chat_calls) - 1],
            "model_used": "test-model",
            "processing_time_ms": 10,
            "voice_url": "/audio/test.wav" if payload["voice_response"] else None,
        }

    def close_session(self, session_id):
        self.closed.append(session_id)
        return True


def _template(*turns):
    return {
        "schema_version": 1,
        "id": "unit_test",
        "name": "Unit test",
        "description": "Test fixture",
        "turns": [{"user": turn} for turn in turns],
        "checks": [{"id": "answered", "type": "all_replies_nonempty"}],
    }


def test_stored_templates_are_valid_and_unique():
    catalog = discover_templates()

    assert set(catalog) >= {"natural_conversation", "operator_assistant"}
    assert all(
        load_template(path)["id"] == template_id
        for template_id, path in catalog.items()
    )


def test_invalid_template_is_rejected():
    template = _template("Hello")
    template["checks"][0]["type"] = "subjective_vibes"

    with pytest.raises(TemplateValidationError, match="unsupported check type"):
        validate_template(template)


def test_checks_must_include_their_required_parameters():
    template = _template("Hello")
    template["checks"][0] = {"id": "short", "type": "max_words", "turn": 1}

    with pytest.raises(TemplateValidationError, match="requires a numeric value"):
        validate_template(template)


def test_phrase_similarity_and_format_checks_are_deterministic():
    transcript = [
        {
            "assistant": "- Keep this\n- Expire that\n- Review both",
            "voice_generated": True,
        },
        {"assistant": "I think privacy matters.", "voice_generated": False},
    ]

    assert evaluate_check(
        {"id": "bullets", "type": "bullet_count", "turn": 1, "value": 3},
        transcript,
    ).passed
    assert evaluate_check(
        {
            "id": "opinion",
            "type": "turn_contains_any",
            "turn": 2,
            "phrases": ["i think", "my view"],
        },
        transcript,
    ).passed
    assert evaluate_check(
        {"id": "voice", "type": "voice_generated", "turn": 1}, transcript
    ).passed
    assert evaluate_check(
        {"id": "different", "type": "max_pair_similarity", "value": 0.8},
        transcript,
    ).passed


def test_run_preserves_continuity_and_always_closes_native_session():
    template = _template("Hello", "What did I just say?")
    client = _FakeClient(["Hi.", "You said hello."])

    result = run_template(template, client)

    assert result.successful
    assert client.user_id.startswith("conversation-eval-unit_test-")
    assert client.chat_calls[0]["session_id"] == "native-session"
    assert client.chat_calls[1]["session_id"] == "native-session"
    assert all(call["ephemeral"] is False for call in client.chat_calls)
    assert client.closed == ["native-session"]


def test_run_closes_session_when_a_later_turn_fails():
    client = _FakeClient(["Hi."], fail_at=2)

    with pytest.raises(RuntimeError, match="simulated failure"):
        run_template(_template("Hello", "Again"), client)

    assert client.closed == ["native-session"]


def test_template_json_contains_no_runtime_identity_or_credentials():
    for path in discover_templates().values():
        payload = path.read_text(encoding="utf-8").casefold()
        assert "api_key" not in payload
        assert "authorization" not in payload
        assert "telegram_id" not in payload
        json.loads(payload)
