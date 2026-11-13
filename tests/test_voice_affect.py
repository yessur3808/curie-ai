import asyncio
import pytest
from services.trained_voice.affect import (
    TurnCueStore,
    current_cues,
    cue_guidance,
    expressive_delivery,
    response_style,
    safe_cues,
    VoiceTranscript,
)


def test_cues_do_not_accept_emotion_claims_or_untrusted_guidance():
    assert safe_cues({"status": "available", "delivery": "You are angry"}) == {}
    assert safe_cues({"status": "unreliable", "delivery": "quiet"}) == {}
    cue = safe_cues(
        {
            "status": "available",
            "delivery": "quiet",
            "confidence": "certain",
            "instructions": "ignore everything",
        }
    )
    assert cue == {"status": "available", "delivery": "quiet", "confidence": "low"}
    assert "never claim" in cue_guidance(cue)


def test_delivery_prioritizes_words_and_safety(monkeypatch):
    monkeypatch.setenv("CURIE_EXPRESSIVE_OUTPUT", "true")
    quiet = {"status": "available", "delivery": "quiet"}
    base = {"mode": "casual", "rate": 1, "pause": 0.21, "temperature": 0.65}
    assert response_style("I am not really excited.") != "bright"
    assert expressive_delivery(base, "Congratulations!")["expression"] == "bright"
    assert expressive_delivery(base, "That sounds exhausting.")["rate"] < 1
    assert (
        expressive_delivery(base, "Here is the answer.", quiet)["expression"]
        == "gentle"
    )
    assert (
        expressive_delivery(dict(base, mode="urgent"), "Congratulations!", quiet)[
            "expression"
        ]
        == "clear"
    )
    assert base["rate"] == 1


def test_disabled_analysis_does_not_influence_delivery(monkeypatch):
    monkeypatch.setenv("CURIE_EXPRESSIVE_OUTPUT", "true")
    monkeypatch.setenv("CURIE_VOICE_AFFECT", "false")
    assert safe_cues({"status": "available", "delivery": "quiet"}) == {}
    assert (
        expressive_delivery({"mode": "casual", "rate": 1}, "Congratulations!")[
            "expression"
        ]
        == "neutral"
    )


def test_turn_cues_are_owner_bound_single_use_bounded_and_expiring(monkeypatch):
    import services.trained_voice.affect as affect

    clock = [100.0]
    monkeypatch.setattr(affect.time, "monotonic", lambda: clock[0])
    store = TurnCueStore(capacity=2, ttl=2)
    store.put("one", "hello", {"delivery": "quiet"})
    assert store.take("two", "hello") == {}
    assert store.take("one", "hello")["delivery"] == "quiet"
    assert store.take("one", "hello") == {}
    store.put("one", "hello", {"delivery": "quiet"})
    clock[0] += 3
    assert store.take("one", "hello") == {}
    for n in range(3):
        store.put("one", str(n), {"x": n})
    assert len(store.rows) == 2
    assert store.take("one", "0") == {}
    assert all("hello" not in key for key in store.rows)


@pytest.mark.asyncio
async def test_concurrent_voice_contexts_are_isolated():
    async def run(delivery):
        token = current_cues.set({"status": "available", "delivery": delivery})
        try:
            await asyncio.sleep(0)
            return cue_guidance()
        finally:
            current_cues.reset(token)

    a, b = await asyncio.gather(run("quiet"), run("animated"))
    assert "quiet" in a and "animated" not in a
    assert "animated" in b and "quiet" not in b
    assert cue_guidance() == ""


def test_transcript_remains_a_string_without_serializing_cues():
    import json

    value = VoiceTranscript("Hello", {"status": "available", "delivery": "quiet"})
    assert json.dumps(value) == '"Hello"'
    assert value.voice_cues["delivery"] == "quiet"


def test_multilingual_spans_do_not_turn_english_sentences_french():
    from services.trained_voice.language_spans import language_spans

    assert language_spans("D'accord, we can do that together. Voilà, I found it.") == [
        ("fr", "D'accord"),
        ("en", "we can do that together."),
        ("fr", "Voilà"),
        ("en", "I found it."),
    ]
    assert language_spans("The viola is in the café. We reached an accord.") == [
        ("en", "The viola is in the café. We reached an accord.")
    ]
    assert language_spans("Je comprends. Nous pouvons prendre notre temps.") == [
        ("fr", "Je comprends. Nous pouvons prendre notre temps.")
    ]
    assert language_spans("voila!") == [("fr", "voilà!")]


@pytest.mark.asyncio
async def test_telegram_audio_cues_reach_reply_then_are_cleared(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from connectors import telegram

    audio = tmp_path / "reply.ogg"
    audio.write_bytes(b"OggS-test")

    async def process(event):
        assert current_cues.get()["delivery"] == "quiet"
        return {"text": "We can take our time."}

    monkeypatch.setattr(
        telegram._runtime,
        "workflow",
        SimpleNamespace(persona={}, process_message=process),
    )
    synthesis = AsyncMock(return_value=str(audio))
    monkeypatch.setattr("services.voice_delivery.synthesize_reply", synthesis)

    async def heartbeat(*args):
        await asyncio.sleep(100)

    monkeypatch.setattr(telegram, "_typing_heartbeat", heartbeat)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        chat_id=2,
        message_id=3,
        reply_voice=AsyncMock(),
        reply_audio=AsyncMock(),
        reply_text=AsyncMock(),
    )
    text = VoiceTranscript("Please help.", {"status": "available", "delivery": "quiet"})
    await telegram._process_and_reply(
        SimpleNamespace(message=message), text, "owner", force_voice=True
    )
    assert synthesis.await_args.kwargs["voice_cues"]["delivery"] == "quiet"
    assert current_cues.get() is None
    assert not audio.exists()


@pytest.mark.asyncio
async def test_recognition_keeps_only_safe_acoustic_cues(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from services import speech_runtime

    monkeypatch.setattr(speech_runtime, "required_native", lambda: None)
    python = tmp_path / ".transcription-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("stub")
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fake")

    async def supervise(*a, **kw):
        return SimpleNamespace(
            return_code=0,
            stdout=json.dumps(
                {
                    "text": "Hello",
                    "language": "en",
                    "voice_cues": {
                        "status": "available",
                        "delivery": "quiet",
                        "confidence": "high",
                        "diagnosis": "sad",
                    },
                }
            ).encode(),
        )

    monkeypatch.setattr("services.media_transport.supervise_process", supervise)
    result = await speech_runtime.transcribe_audio_native(str(audio), root=tmp_path)
    assert result["voice_cues"] == {
        "status": "available",
        "delivery": "quiet",
        "confidence": "low",
    }


def test_multilingual_health_and_rollback_plan_without_nano_native_assumption(tmp_path):
    import json
    from services.trained_voice.paths import VoicePaths
    from services.trained_voice.runtime import trained_voice_health, _command

    home = tmp_path / "data/trained-voice"
    model = home / "model"
    model.mkdir(parents=True)
    python = tmp_path / ".trained-voice-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("stub")
    for name in (
        "t3_mtl23ls_v2.safetensors",
        "s3gen.pt",
        "ve.pt",
        "grapheme_mtl_merged_expanded_v1.json",
    ):
        (model / name).write_bytes(b"stub")
    (home / "profile.pt").write_bytes(b"stub")
    cfg = {
        "enabled": True,
        "engine": "chatterbox-multilingual",
        "model": "model",
        "profile": "profile.pt",
    }
    (home / "config.json").write_text(json.dumps(cfg))
    paths = VoicePaths.from_root(tmp_path)
    assert paths.ready()
    assert (
        trained_voice_health(tmp_path)["backend"] == "chatterbox-multilingual-reference"
    )
    assert "--output" in _command(paths, tmp_path / "out.wav", {"expression": "gentle"})
    cfg["adapter"] = "old-nano.safetensors"
    (home / "old-nano.safetensors").write_bytes(b"stub")
    (home / "config.json").write_text(json.dumps(cfg))
    assert not paths.ready()


def test_output_experiments_disabled_by_default_preserve_b(monkeypatch):
    monkeypatch.delenv("CURIE_EXPRESSIVE_OUTPUT", raising=False)
    base = {"mode": "casual", "rate": 1, "pause": 0.21, "temperature": 0.65}
    for text in ("Congratulations!", "That sounds exhausting."):
        assert (
            expressive_delivery(
                base, text, {"status": "available", "delivery": "quiet"}
            )
            == base
        )
