import asyncio
import pytest
from agent.orchestration.contracts import ResponseCandidate


@pytest.mark.asyncio
async def test_interruption_is_context_not_claim_that_full_reply_was_heard(monkeypatch):
    from services.trained_voice import api

    captured = []

    async def generate(prompt, *args, **kwargs):
        captured.append((prompt, kwargs))
        return ResponseCandidate(
            "Which stop should I use for the afternoon trip?", "test"
        )

    class Request:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(api, "voice_session_manager", lambda: None)
    monkeypatch.setattr(api, "gate", asyncio.Semaphore(1))
    monkeypatch.setattr(api.model, "generate", generate)
    result = await api.chat(
        api.Message(
            message="Actually, I meant the afternoon trip. Ask me which stop without guessing.",
            user_id="test-interruption",
            live=True,
            ephemeral=True,
            previous_reply_interrupted=True,
        ),
        Request(),
    )
    assert "Which stop" in result["text"]
    assert "not proof the owner heard it" in str(captured[0][0])
    assert captured[0][1]["capability_scope"] == "conversation_only"


def test_explicit_question_after_correction_survives_live_cleanup():
    from services.trained_voice.voice_persona import finalize_live_response

    reply = "Right. Which stop should I use?"
    assert (
        finalize_live_response(
            reply,
            "Actually, I meant the afternoon trip. Ask me which stop without guessing.",
            False,
        )
        == reply
    )
