import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.voice_delivery import voice_reply_requested


@pytest.mark.parametrize(
    "text",
    [
        "Send me a voice note asking me how I am doing",
        "Could you send me an audio note?",
        "Please send that as a voice message",
        "Reply by audio",
    ],
)
def test_voice_note_requests_are_recognized(text):
    assert voice_reply_requested(text)


@pytest.mark.parametrize(
    "text",
    [
        "Don't send me a voice note",
        "Reply in text only, no voice messages",
        "Tell me how voice synthesis works",
    ],
)
def test_voice_requests_respect_text_only(text):
    assert not voice_reply_requested(text)


def test_model_gets_actual_voice_delivery_guidance(monkeypatch):
    from agent.orchestration.model_service import ModelConversationService

    monkeypatch.setattr(
        "contracts.model_capability_prompt", lambda **kw: "Capabilities"
    )
    monkeypatch.setattr("services.voice_delivery.voice_health", lambda: {"ready": True})
    prompt = ModelConversationService.capability_aware_prompt(
        "base", "Send me a voice note asking me how I am doing"
    )
    assert "provide the words to speak" in prompt
    assert "do not claim audio has already" in prompt
    monkeypatch.setattr(
        "services.voice_delivery.voice_health", lambda: {"ready": False}
    )
    assert (
        "local speech synthesis is ready"
        not in ModelConversationService.capability_aware_prompt(
            "base", "Send me a voice note"
        )
    )


@pytest.mark.asyncio
async def test_exact_request_sends_voice_and_deletes_audio(monkeypatch, tmp_path):
    from connectors import telegram

    audio = tmp_path / "reply.ogg"
    audio.write_bytes(b"OggS-test")
    workflow = SimpleNamespace(
        persona={},
        process_message=AsyncMock(return_value={"text": "How are you doing today?"}),
    )
    monkeypatch.setattr(telegram._runtime, "workflow", workflow)
    synthesis = AsyncMock(return_value=str(audio))
    monkeypatch.setattr("services.voice_delivery.synthesize_reply", synthesis)
    monkeypatch.setattr(
        "services.voice_delivery.voice_replies_enabled", lambda *args: False
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        chat_id=2,
        message_id=3,
        reply_voice=AsyncMock(),
        reply_audio=AsyncMock(),
        reply_text=AsyncMock(),
    )

    async def heartbeat(*args):
        await asyncio.sleep(100)

    monkeypatch.setattr(telegram, "_typing_heartbeat", heartbeat)
    await telegram._process_and_reply(
        SimpleNamespace(message=message),
        "Send me a voice note asking me how I am doing",
        "test-owner",
    )
    synthesis.assert_awaited_once_with("How are you doing today?", {}, "test-owner")
    message.reply_voice.assert_awaited_once()
    message.reply_text.assert_not_awaited()
    assert not audio.exists()


@pytest.mark.parametrize(
    "text",
    [
        "Switch to text",
        "Please switch back to text mode",
        "Can you change to text please?",
        "Stop sending voice messages",
        "Stop voice replies",
        "Don't send me voice messages",
        "Text only please",
    ],
)
def test_text_switch_persists_and_suppresses_voice(text, tmp_path, monkeypatch):
    from memory import local_store
    from memory.adaptation import (
        apply_voice_modality_preference,
        handle_adaptation_command,
    )
    from services.voice_delivery import voice_input_reply_allowed, voice_replies_enabled

    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    handle_adaptation_command("u1", "/voice on", "telegram")
    assert apply_voice_modality_preference("u1", text, "telegram") is False
    assert not voice_replies_enabled("u1", "telegram")
    assert not voice_input_reply_allowed("u1", "telegram")
    assert not voice_reply_requested(text)
    assert voice_input_reply_allowed("u1", "slack")
    handle_adaptation_command("u1", "/voice on", "telegram")
    assert voice_input_reply_allowed("u1", "telegram")


@pytest.mark.parametrize(
    "text",
    [
        "Don't switch to text",
        "How does speech to text work?",
        "Convert this audio to text",
    ],
)
def test_text_switch_does_not_capture_unrelated_or_negated_requests(text):
    from memory.adaptation import text_reply_requested

    assert not text_reply_requested(text)


@pytest.mark.asyncio
async def test_spoken_text_switch_and_subsequent_audio_stay_text(monkeypatch, tmp_path):
    from connectors import telegram
    from memory import local_store
    from memory.adaptation import handle_adaptation_command

    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    handle_adaptation_command("u1", "/voice on", "telegram")
    workflow = SimpleNamespace(
        persona={},
        process_message=AsyncMock(return_value={"text": "Here is my reply."}),
    )
    monkeypatch.setattr(telegram._runtime, "workflow", workflow)
    synthesis = AsyncMock()
    monkeypatch.setattr("services.voice_delivery.synthesize_reply", synthesis)
    reply = AsyncMock()
    monkeypatch.setattr(telegram, "reply_with_result", reply)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        chat_id=2,
        message_id=3,
        reply_voice=AsyncMock(),
        reply_audio=AsyncMock(),
    )

    async def heartbeat(*args):
        await asyncio.sleep(100)

    monkeypatch.setattr(telegram, "_typing_heartbeat", heartbeat)
    for text in ("Switch to text please", "What is the weather?"):
        await telegram._process_and_reply(
            SimpleNamespace(message=message), text, "u1", force_voice=True
        )
    synthesis.assert_not_awaited()
    message.reply_voice.assert_not_awaited()
    assert reply.await_count == 2
