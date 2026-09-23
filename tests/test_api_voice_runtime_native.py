"""Native API, live-session, recognition, and trained-speech contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

native = pytest.importorskip("_curie_api_voice_runtime")


def decoded(value):
    return json.loads(value)


def test_api_admission_fencing_replay_and_owner_limits():
    coordinator = native.ApiRequestCoordinator(2, 1, 8)
    first = decoded(coordinator.begin("one", "owner", "chat", 1_000, 10_000))
    assert first["outcome"] == "admitted"
    assert (
        decoded(coordinator.begin("two", "owner", "chat", 1_001))["outcome"]
        == "owner_busy"
    )
    with pytest.raises(PermissionError, match="lease_stale"):
        coordinator.finish("one", first["lease_token"] + 1, "{}", 1_002)
    response = {"text": "Bonjour", "model_used": "local"}
    assert coordinator.finish(
        "one", first["lease_token"], json.dumps(response), 1_003, 5_000
    )
    replay = decoded(coordinator.begin("one", "owner", "chat", 1_004))
    assert replay == {"outcome": "replay", "response": response}


def test_inference_queue_priority_cancellation_and_metrics():
    coordinator = native.InferenceCoordinator(2, 1)
    coordinator.submit("background", "b", "background", 100)
    coordinator.submit("active", "a", "active", 101)
    assert decoded(coordinator.pop(110))["request_id"] == "active"
    assert coordinator.finish("active", "completed")
    cancelled = decoded(coordinator.cancel("background"))
    assert cancelled == {"found": True, "was_running": False}
    snapshot = decoded(coordinator.snapshot())
    assert snapshot["completed"] == 1
    assert snapshot["cancelled"] == 1


def test_live_session_fencing_history_and_artifact_expiry():
    sessions = native.VoiceSessionManager(4, 2)
    opened = decoded(sessions.open("owner", "dashboard", 1_000, 60_000))
    session_id = opened["session_id"]
    started = decoded(sessions.start_operation(session_id, "chat", 1_001))
    assert started["state"] == "thinking"
    assert (
        decoded(sessions.start_operation(session_id, "synthesis", 1_002))["outcome"]
        == "busy"
    )
    transitioned = decoded(
        sessions.transition(session_id, started["token"], "synthesizing", 1_003)
    )
    assert transitioned["state"] == "synthesizing"
    with pytest.raises(PermissionError, match="token_stale"):
        sessions.finish_operation(session_id, started["token"] + 1, 1_004)
    assert sessions.finish_operation(session_id, started["token"], 1_005)

    for index in range(3):
        sessions.append_history(session_id, f"user-{index}", f"assistant-{index}")
    history = decoded(sessions.history(session_id))
    assert [turn["user"] for turn in history] == ["user-1", "user-2"]

    name = "voice_550e8400-e29b-41d4-a716-446655440000.ogg"
    sessions.register_artifact(name, 2_000, 1_000)
    assert sessions.expired_artifacts(2_999, 10) == []
    assert sessions.expired_artifacts(3_000, 10) == [name]


def test_parallel_voice_turn_admission_allows_exactly_one_operation():
    sessions = native.VoiceSessionManager(4, 2)
    session_id = decoded(sessions.open("owner", "live", 1_000))["session_id"]

    def start(_):
        return decoded(sessions.start_operation(session_id, "chat", 1_001))["outcome"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(start, range(8)))
    assert outcomes.count("started") == 1
    assert outcomes.count("busy") == 7


def test_request_and_worker_inputs_are_validated(tmp_path: Path):
    request = decoded(native.validate_api_message("owner", "Turn the lights off", None))
    assert request["user_id"] == "owner"
    with pytest.raises(ValueError, match="idempotency"):
        native.validate_api_message("owner", "hello", "../bad")

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"OggS-not-empty")
    python = tmp_path / "python"
    python.write_text("worker")
    plan = decoded(
        native.transcription_plan(
            str(tmp_path), str(python), str(audio), "en-US", "french", True
        )
    )
    assert plan["language_hint"] == "fr"
    transcript = decoded(
        native.parse_transcription(
            b'diagnostic\n{"text":"  bonjour   monsieur ","language":"fr"}\n'
        )
    )
    assert transcript == {"text": "bonjour monsieur", "language": "fr"}


def _voice_fixture(root: Path) -> tuple[Path, Path]:
    home = root / "voice"
    model = home / "model"
    model.mkdir(parents=True)
    (home / "profile.wav").write_bytes(b"RIFF")
    for name in (
        "t3_nano_v1.safetensors",
        "s3gen_meanflow.safetensors",
        "ve.safetensors",
        "tokenizer_config.json",
    ):
        (model / name).write_bytes(b"model")
    config = {
        "enabled": True,
        "engine": "chatterbox-nano",
        "profile": "profile.wav",
        "model": "model",
        "adapter": "",
        "method": "reference",
        "revision": "test",
        "threads": 2,
    }
    (home / "config.json").write_text(json.dumps(config))
    python = root / "speech-python"
    python.write_text("worker")
    return home, python


def test_trained_speech_plan_metrics_events_and_cross_process_lease(tmp_path: Path):
    home, python = _voice_fixture(tmp_path)
    health = decoded(native.trained_voice_health(str(home), str(python)))
    assert health["ready"] is True
    assert health["coordinator"] == "rust"
    plan = decoded(
        native.synthesis_plan(
            str(tmp_path),
            str(home),
            str(python),
            str(tmp_path / "out.wav"),
            '{"rate":1.0}',
        )
    )
    assert plan["command"][2] == "services.trained_voice.reference_voice"
    metrics = decoded(
        native.parse_voice_metrics(
            b'noise\n{"engine":"nano","seconds":1.2,"ignored":"removed"}\n'
        )
    )
    assert metrics == {"engine": "nano", "seconds": 1.2}
    event = decoded(
        native.parse_voice_event(b'{"type":"audio","url":"/audio/voice_abc.wav"}')
    )
    assert event["type"] == "audio"

    lock = tmp_path / "runtime.lock"
    first = native.acquire_voice_lease(str(lock), 0, 10)
    try:
        with pytest.raises(RuntimeError, match="trained_voice_busy"):
            native.acquire_voice_lease(str(lock), 0, 10)
    finally:
        assert first.release()


@pytest.mark.asyncio
async def test_speech_runtime_uses_native_plan_and_bounded_media_worker(
    monkeypatch, tmp_path: Path
):
    from services import api_voice_runtime, media_transport
    from services.speech_runtime import transcribe_audio_native

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF-not-empty")
    python = tmp_path / "python"
    python.write_text("worker")
    monkeypatch.setenv("CURIE_API_VOICE_RUNTIME", "rust")
    monkeypatch.setenv("CURIE_TRANSCRIBE_PYTHON", str(python))
    api_voice_runtime.reset_api_voice_runtime()

    calls = []

    async def fake_supervise(command, stdin_data=b"", **kwargs):
        calls.append((command, kwargs))
        return media_transport.ProcessResult(
            0, b'{"text":"hello Curie","language":"en"}\n', 1.0, "rust"
        )

    monkeypatch.setattr(media_transport, "supervise_process", fake_supervise)
    result = await transcribe_audio_native(
        str(audio), language="en", auto_detect=False, root=tmp_path
    )
    assert result["text"] == "hello Curie"
    assert calls[0][0][2] == "services.trained_voice.transcribe"


@pytest.mark.asyncio
async def test_speech_runtime_retains_isolated_python_rollback(
    monkeypatch, tmp_path: Path
):
    from services import api_voice_runtime, media_transport
    from services.speech_runtime import transcribe_audio_native

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF-not-empty")
    python = tmp_path / "python"
    python.write_text("worker")
    monkeypatch.setenv("CURIE_API_VOICE_RUNTIME", "python")
    monkeypatch.setenv("CURIE_TRANSCRIBE_PYTHON", str(python))
    api_voice_runtime.reset_api_voice_runtime()

    async def fake_supervise(command, stdin_data=b"", **kwargs):
        return media_transport.ProcessResult(
            0, b'{"text":"fallback works","language":"en"}\n', 1.0, "python"
        )

    monkeypatch.setattr(media_transport, "supervise_process", fake_supervise)
    result = await transcribe_audio_native(
        str(audio), language="en", auto_detect=False, root=tmp_path
    )
    assert result["text"] == "fallback works"
    assert result["backend"].endswith("python-rollback")
