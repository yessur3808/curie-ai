"""Rust-coordinated speech-recognition worker integration."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path

from services.api_voice_runtime import required_native
from services.trained_voice.paths import VoicePaths

ROOT = Path(__file__).resolve().parents[1]
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "CURIE_VOICE_AFFECT",
)


class SpeechRecognitionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def speech_recognition_status(root: Path | None = None) -> dict:
    from services.api_voice_runtime import api_voice_runtime_status

    runtime = api_voice_runtime_status()
    paths = VoicePaths.from_root(root or ROOT)
    ready = (
        paths.transcription_python.is_file()
        if runtime["active"] == "rust"
        else bool(importlib.util.find_spec("whisper"))
    )
    return {
        "ready": ready,
        "coordinator": runtime["active"],
        "backend": "faster-whisper-isolated",
        "worker_isolated": True,
    }


def _environment() -> dict[str, str]:
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


async def transcribe_audio_native(
    audio_path: str,
    *,
    language: str = "en",
    accent: str | None = None,
    auto_detect: bool = True,
    root: Path | None = None,
    warm: bool = False,
) -> dict[str, str]:
    native = required_native()
    selected_root = (root or ROOT).resolve()
    paths = VoicePaths.from_root(selected_root)
    if native is not None:
        try:
            plan = json.loads(
                native.transcription_plan(
                    str(selected_root),
                    str(paths.transcription_python),
                    str(audio_path),
                    language,
                    accent,
                    auto_detect,
                    int(os.getenv("CURIE_TRANSCRIBE_MAX_BYTES", str(25 * 1024 * 1024))),
                )
            )
        except (RuntimeError, ValueError) as exc:
            raise SpeechRecognitionError(str(exc)) from exc
    else:
        source = Path(audio_path)
        maximum = int(os.getenv("CURIE_TRANSCRIBE_MAX_BYTES", str(25 * 1024 * 1024)))
        suffix = source.suffix.casefold()
        if (
            not source.is_file()
            or source.is_symlink()
            or not 0 < source.stat().st_size <= maximum
            or suffix not in {".wav", ".ogg", ".m4a", ".mp3", ".webm", ".opus", ".flac"}
        ):
            raise SpeechRecognitionError("speech_audio_invalid")
        if not paths.transcription_python.is_file():
            raise SpeechRecognitionError("speech_worker_unavailable")
        language_hint = None if auto_detect else language.split("-", 1)[0].lower()
        command = [
            str(paths.transcription_python),
            "-m",
            "services.trained_voice.transcribe",
            str(source),
        ]
        if language_hint:
            command.append(language_hint)
        plan = {
            "backend": "faster-whisper-isolated-python-rollback",
            "command": command,
            "cwd": str(selected_root),
        }

    from services.media_transport import supervise_process

    try:
        result = None
        if warm and os.getenv("CURIE_WARM_TRANSCRIPTION", "true").lower() == "true":
            from services.trained_voice.warm_worker import recognition_worker
            from types import SimpleNamespace

            try:
                worker = recognition_worker(
                    selected_root, paths.transcription_python, _environment()
                )
                transcript = await worker.transcribe(
                    audio_path,
                    None if auto_detect else language.split("-", 1)[0].lower(),
                    timeout=float(os.getenv("CURIE_TRANSCRIBE_TIMEOUT_SECONDS", "110")),
                )
                result = SimpleNamespace(
                    return_code=0, stdout=json.dumps(transcript).encode()
                )
                plan["backend"] = "faster-whisper-isolated-warm"
            except (RuntimeError, ValueError, OSError):
                # A broken resident worker falls back to the proven one-shot path.
                result = None
        if result is None:
            result = await supervise_process(
                plan["command"],
                cwd=plan["cwd"],
                environment=_environment(),
                timeout=float(os.getenv("CURIE_TRANSCRIBE_TIMEOUT_SECONDS", "110")),
                max_stdout_bytes=1024 * 1024,
            )
    except TimeoutError as exc:
        raise SpeechRecognitionError("speech_worker_timeout") from exc
    if result.return_code:
        raise SpeechRecognitionError("speech_worker_failed")
    try:
        if native is not None:
            parsed = json.loads(native.parse_transcription(result.stdout, 10_000))
        else:
            parsed = json.loads(result.stdout.decode().splitlines()[-1])
            parsed["text"] = " ".join(str(parsed.get("text") or "").split())
            if not parsed["text"] or len(parsed["text"]) > 10_000:
                raise ValueError("speech_transcript_invalid")
    except (IndexError, UnicodeError, RuntimeError, ValueError) as exc:
        raise SpeechRecognitionError("speech_worker_output_invalid") from exc
    from services.trained_voice.affect import safe_cues

    raw = json.loads(result.stdout.decode().splitlines()[-1])
    return {
        "voice_cues": safe_cues(raw.get("voice_cues")),
        "text": str(parsed["text"]),
        "language": str(parsed["language"]),
        "backend": plan["backend"],
    }


__all__ = [
    "SpeechRecognitionError",
    "speech_recognition_status",
    "transcribe_audio_native",
]
