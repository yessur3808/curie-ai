import asyncio
import json
from contextlib import asynccontextmanager
import pytest


@pytest.mark.asyncio
async def test_stream_reports_first_audio_and_total_without_text(monkeypatch, tmp_path):
    from services.trained_voice import api

    @asynccontextmanager
    async def operation(*args, **kwargs):
        yield "test-session", None

    async def stream(*args, **kwargs):
        await asyncio.sleep(0.001)
        yield {"type": "audio", "url": "/audio/test.wav", "duration": 1}

    monkeypatch.setattr(api, "_voice_operation", operation)
    monkeypatch.setattr(api, "refresh_persona", lambda: {"name": "Curie"})
    monkeypatch.setattr(api, "reference_voice_ready", lambda: True)
    monkeypatch.setattr(api, "audio_dir", tmp_path)
    monkeypatch.setattr(api, "trained_voice_stream_events", stream)
    response = await api.speak_stream(api.Speech(text="Hello", user_id="timing-test"))
    events = [json.loads(x) async for x in response.body_iterator]
    timings = events[-1]["timings"]
    assert events[-1]["type"] == "done"
    assert 0 <= timings["first_audio_ms"] <= timings["synthesis_total_ms"]
    assert set(timings) == {"first_audio_ms", "synthesis_total_ms"}


@pytest.mark.asyncio
async def test_chat_times_reply_separately_from_speech(monkeypatch):
    from services.trained_voice import api
    from agent.orchestration.contracts import ResponseCandidate

    async def generate(*args, **kwargs):
        return ResponseCandidate("The answer is ready.", "test")

    class Request:
        async def is_disconnected(self):
            return False

    monkeypatch.setattr(api, "voice_session_manager", lambda: None)
    monkeypatch.setattr(api, "gate", asyncio.Semaphore(1))
    monkeypatch.setattr(api.model, "generate", generate)
    result = await api.chat(
        api.Message(
            message="Explain the next step briefly.",
            user_id="timing-test",
            live=True,
            ephemeral=True,
        ),
        Request(),
    )
    assert 0 <= result["timings"]["reply_ready_ms"] <= result["processing_time_ms"]
    assert result["timings"]["speech_ms"] == 0
