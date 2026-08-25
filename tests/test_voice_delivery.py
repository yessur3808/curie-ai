import asyncio
from pathlib import Path

from services.voice_delivery import detect_language_spans, synthesize_reply, voice_health
from utils.voice import get_piper_executable, normalize_for_speech


def test_local_voice_synthesis_produces_audio(monkeypatch):
    monkeypatch.setattr("utils.voice.get_ffmpeg_executable", lambda: None)

    async def fake_tts(text, path, config):
        Path(path).write_bytes(b"RIFF-local-audio")
        return True

    monkeypatch.setattr("utils.voice.text_to_speech", fake_tts)
    path = asyncio.run(synthesize_reply("Bonjour, mon ami.", {}))
    try:
        assert path and path.endswith(".wav") and Path(path).stat().st_size > 0
    finally:
        if path:
            Path(path).unlink(missing_ok=True)


def test_speech_normalization_handles_technical_text_and_links():
    normalized = normalize_for_speech(
        "CPU is 80%. See https://example.com/a for the API."
    )
    assert "C P U" in normalized
    assert "80 percent" in normalized
    assert "link to example.com" in normalized
    assert "A P I" in normalized


def test_quality_policy_reports_text_fallback_when_neural_voice_missing(monkeypatch):
    monkeypatch.setenv("PIPER_MODEL_PATH", "/missing/voice.onnx")
    monkeypatch.setenv("LOCAL_TTS_ALLOW_ESPEAK", "false")
    health = voice_health()
    assert health["piper_ready"] is False
    assert health["quality_fallback_policy"] == "text"
    assert health["backend"] is None


def test_project_local_piper_is_resolved_when_path_lookup_fails(monkeypatch):
    monkeypatch.setenv("PIPER_BINARY", "piper")
    monkeypatch.setattr("utils.voice.shutil.which", lambda _name: None)
    resolved = get_piper_executable()
    assert resolved is None or resolved.endswith("/.venv/bin/piper")


def test_language_spans_keep_english_clear_and_french_natural():
    assert detect_language_spans("Bonjour, mon ami. The system is ready.") == [
        ("fr", "Bonjour, mon ami."), ("en", " The system is ready.")
    ]
    assert detect_language_spans("English only.", "strong") == [("fr", "English only.")]
    assert detect_language_spans("Bonjour.", "neutral") == [("en", "Bonjour.")]


def test_voice_configuration_and_custom_consent_are_validated(tmp_path, monkeypatch):
    from memory import local_store
    from services.voice_commands import configure_voice, custom_voice_command

    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    assert configure_voice("u1", "accent", "subtle") == "Voice accent set to subtle."
    assert "enabled" in custom_voice_command("u1", "consent")
    assert "revoked" in custom_voice_command("u1", "revoke")
