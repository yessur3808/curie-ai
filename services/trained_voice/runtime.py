"""Shared, model-lazy bridge to Curie's isolated trained speech worker."""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
import time
from typing import Any, AsyncIterator, Mapping

from .paths import VoicePaths
from .voice_stream import worker_events

ROOT = Path(__file__).resolve().parents[2]
_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "XDG_CACHE_HOME",
)


class TrainedVoiceError(RuntimeError):
    """Safe trained-voice failure without dependency stderr or spoken text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TrainedVoiceBusy(TrainedVoiceError):
    pass


def trained_voice_required() -> bool:
    return os.getenv("CURIE_TRAINED_VOICE_REQUIRED", "true").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _paths(root: Path | None = None) -> VoicePaths:
    return VoicePaths.from_root(root or ROOT)


def _config(paths: VoicePaths) -> dict[str, Any]:
    try:
        value = json.loads(paths.config.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, Mapping) else {}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def trained_voice_health(root: Path | None = None) -> dict[str, Any]:
    """Read readiness metadata without importing Torch or model packages."""
    paths = _paths(root)
    config = _config(paths)
    ready = paths.ready()
    return {
        "ready": ready,
        "backend": "chatterbox-nano-trained" if ready else None,
        "engine": config.get("engine"),
        "trained": bool(config.get("adapter")),
        "method": config.get("method"),
        "revision": config.get("revision"),
        "worker_isolated": True,
        "threads": int(config.get("threads") or 0),
    }


def _worker_environment() -> dict[str, str]:
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


def _timeout_seconds() -> float:
    try:
        value = float(os.getenv("CURIE_TRAINED_VOICE_TIMEOUT_SECONDS", "115"))
    except (TypeError, ValueError):
        value = 115.0
    return max(30.0, min(300.0, value))


def _queue_seconds() -> float:
    try:
        value = float(os.getenv("CURIE_TRAINED_VOICE_QUEUE_SECONDS", "3"))
    except (TypeError, ValueError):
        value = 3.0
    return max(0.0, min(30.0, value))


def _lock_path(paths: VoicePaths) -> Path:
    configured = os.getenv("CURIE_TRAINED_VOICE_LOCK_PATH", "").strip()
    return Path(configured).expanduser() if configured else paths.home / "runtime.lock"


async def _acquire_file_lock(path: Path, wait_seconds: float) -> int:
    """Acquire one cross-process worker slot without blocking the event loop."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(path, 0o600)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise TrainedVoiceBusy("trained_voice_busy")
            await asyncio.sleep(0.1)


@asynccontextmanager
async def trained_voice_lease(
    paths: VoicePaths | None = None,
) -> AsyncIterator[VoicePaths]:
    import fcntl

    selected = paths or _paths()
    if not selected.ready():
        raise TrainedVoiceError("trained_voice_unavailable")
    descriptor = await _acquire_file_lock(_lock_path(selected), _queue_seconds())
    try:
        yield selected
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _command(
    paths: VoicePaths,
    output: str | Path,
    delivery: Mapping[str, Any],
    *,
    stream_dir: str | Path | None = None,
) -> list[str]:
    command = [
        str(paths.speech_python),
        "-m",
        "services.trained_voice.reference_voice",
        "--config",
        str(paths.config),
        "--output",
        str(output),
        "--delivery",
        json.dumps(dict(delivery), separators=(",", ":")),
    ]
    if stream_dir is not None:
        command.extend(("--stream-dir", str(stream_dir)))
    return command


def _safe_metrics(stdout: bytes) -> dict[str, Any]:
    allowed = {
        "engine",
        "method",
        "revision",
        "deliveryMode",
        "seconds",
        "generationSeconds",
        "peakMiB",
        "chunks",
        "retries",
    }
    for line in reversed(stdout.decode(errors="replace").splitlines()):
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, Mapping):
            return {key: value[key] for key in allowed if key in value}
    return {}


async def synthesize_trained_voice(
    text: str,
    output: str | Path,
    delivery: Mapping[str, Any],
) -> dict[str, Any]:
    """Generate one WAV using only the trained worker or fail explicitly."""
    if not str(text).strip() or len(text) > 24000:
        raise TrainedVoiceError("trained_voice_invalid_text")
    output_path = Path(output)
    paths = _paths()
    try:
        async with trained_voice_lease(paths):
            process = await asyncio.create_subprocess_exec(
                *_command(paths, output_path, delivery),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(paths.root),
                env=_worker_environment(),
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(text.encode()), timeout=_timeout_seconds()
                )
            except (asyncio.TimeoutError, asyncio.CancelledError) as error:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise TrainedVoiceError("trained_voice_timeout") from error
            if process.returncode:
                raise TrainedVoiceError("trained_voice_worker_failed")
            if not output_path.is_file() or output_path.stat().st_size <= 44:
                raise TrainedVoiceError("trained_voice_empty_output")
            return _safe_metrics(stdout)
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise


async def trained_voice_stream_events(
    text: str,
    output: str | Path,
    stream_dir: str | Path,
    delivery: Mapping[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Stream trained sentence files while holding the shared worker lease."""
    paths = _paths()
    async with trained_voice_lease(paths):
        stream = worker_events(
            _command(paths, output, delivery, stream_dir=stream_dir),
            text,
            env=_worker_environment(),
            cwd=str(paths.root),
            timeout=_timeout_seconds(),
        )
        try:
            async for event in stream:
                yield event
        finally:
            await stream.aclose()


__all__ = [
    "TrainedVoiceBusy",
    "TrainedVoiceError",
    "synthesize_trained_voice",
    "trained_voice_health",
    "trained_voice_lease",
    "trained_voice_required",
    "trained_voice_stream_events",
]
