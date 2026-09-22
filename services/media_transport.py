"""Bounded audio/media transport with an optional Rust execution kernel.

This layer deliberately owns bytes and child-process mechanics only. Connector
presentation, speech models, transcription, attachment policy, and Curie's
personality stay in Python.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
from typing import AsyncIterator, Mapping, Sequence
import wave

_NATIVE_MODULE = None
_NATIVE_IMPORT_ATTEMPTED = False
_NATIVE_IMPORT_ERROR: str | None = None
_METRICS_LOCK = threading.Lock()
_METRICS = {
    "inspections": 0,
    "bytes_inspected": 0,
    "native_operations": 0,
    "python_operations": 0,
    "processes": 0,
    "timeouts": 0,
    "cancellations": 0,
    "wav_concatenations": 0,
    "wav_manifests": 0,
    "failures": 0,
    "total_latency_ms": 0.0,
}
_BUFFER_BYTES = 64 * 1024
_SIGNATURE_BYTES = 4096
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


class MediaTransportError(ValueError):
    """Stable, content-free transport rejection."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or _ERROR_MESSAGES.get(code, "Media transport failed"))
        self.code = code


_ERROR_MESSAGES = {
    "attachment_not_regular": "Attachment must be a regular local file",
    "attachment_empty": "Attachment is empty or exceeds the configured size limit",
    "attachment_too_large": "Attachment is empty or exceeds the configured size limit",
    "attachment_unreadable": "Attachment could not be inspected safely",
    "attachment_changed_during_inspection": "Attachment changed while it was being inspected",
    "media_signature_executable": "Executable attachments are not accepted",
    "media_signature_mismatch": "Attachment content does not match its declared media type",
    "wav_sources_empty": "No audio spans were provided",
    "wav_not_regular": "Audio input must be a regular local file",
    "wav_size_invalid": "Audio input is empty or exceeds the configured size limit",
    "wav_header_invalid": "Audio input is not a valid WAV file",
    "wav_chunk_invalid": "Audio input contains a malformed WAV chunk",
    "wav_format_invalid": "Audio input has an invalid WAV format",
    "wav_format_unsupported": "Audio input uses an unsupported WAV format",
    "wav_format_missing": "Audio input has no WAV format chunk",
    "wav_data_missing": "Audio input has no PCM data",
    "wav_data_invalid": "Audio input contains invalid PCM data",
    "wav_data_truncated": "Audio input ended before its PCM data was complete",
    "wav_format_mismatch": "Audio spans do not share one PCM format",
    "wav_output_too_large": "Combined audio exceeds the configured size limit",
    "wav_output_failed": "Combined audio could not be written safely",
    "media_worker_command_empty": "Media worker command is empty",
    "media_worker_spawn_failed": "Media worker could not be started",
    "media_worker_wait_failed": "Media worker could not be supervised",
    "media_worker_stdout_unavailable": "Media worker output is unavailable",
    "media_worker_stdout_failed": "Media worker output could not be read",
    "media_worker_stdout_limit": "Media worker exceeded its output limit",
}


@dataclass(frozen=True, slots=True)
class ProcessResult:
    return_code: int
    stdout: bytes
    duration_ms: float
    backend: str

    def as_dict(self) -> dict:
        return asdict(self)


def _mode() -> str:
    configured = os.getenv("CURIE_MEDIA_TRANSPORT", "auto").strip().casefold()
    return configured if configured in {"auto", "rust", "python"} else "auto"


def _native_module():
    global _NATIVE_MODULE, _NATIVE_IMPORT_ATTEMPTED, _NATIVE_IMPORT_ERROR
    if _NATIVE_IMPORT_ATTEMPTED:
        return _NATIVE_MODULE
    _NATIVE_IMPORT_ATTEMPTED = True
    try:
        import _curie_media_transport as native

        _NATIVE_MODULE = native
        _NATIVE_IMPORT_ERROR = None
    except (ImportError, OSError) as exc:
        _NATIVE_IMPORT_ERROR = type(exc).__name__
        _NATIVE_MODULE = None
    return _NATIVE_MODULE


def media_transport_status() -> dict:
    """Return content-free readiness and active backend information."""
    mode = _mode()
    native = _native_module()
    available = native is not None
    version = None
    if available:
        try:
            version = str(native.transport_version())
        except Exception:
            available = False
    active = "rust" if available and mode != "python" else "python"
    return {
        "mode": mode,
        "available": available,
        "active": active,
        "version": version,
        "fallback": mode != "rust",
        "import_error": _NATIVE_IMPORT_ERROR,
    }


def _required_native():
    status = media_transport_status()
    if status["mode"] == "rust" and status["active"] != "rust":
        raise RuntimeError(
            "CURIE_MEDIA_TRANSPORT=rust but the native module is unavailable"
        )
    return _native_module() if status["active"] == "rust" else None


def _record(**values) -> None:
    with _METRICS_LOCK:
        for key, value in values.items():
            if key in _METRICS:
                _METRICS[key] += value


def media_transport_metrics(*, reset: bool = False) -> dict:
    with _METRICS_LOCK:
        result = dict(_METRICS)
        if reset:
            for key in _METRICS:
                _METRICS[key] = 0.0 if key == "total_latency_ms" else 0
    result["total_latency_ms"] = round(float(result["total_latency_ms"]), 2)
    result["kernel"] = media_transport_status()
    return result


def _suffix_kind(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        return "image"
    if suffix in {".mp3", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".flac", ".webm"}:
        return "audio"
    if suffix in {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".xml",
        ".pdf",
        ".docx",
        ".odt",
    }:
        return "document"
    if suffix in {
        ".apk",
        ".app",
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".dmg",
        ".exe",
        ".iso",
        ".jar",
        ".msi",
        ".ps1",
        ".scr",
        ".sh",
    }:
        return "executable"
    return "unknown"


def _mime_kind(content_type: str) -> str:
    mime = str(content_type or "").strip().casefold()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or mime in {
        "application/pdf",
        "application/json",
        "application/xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.oasis.opendocument.text",
    }:
        return "document"
    if mime in {
        "application/x-dosexec",
        "application/x-executable",
        "application/x-sharedlib",
        "application/java-archive",
    }:
        return "executable"
    return "unknown"


def _sniff_signature(data: bytes) -> tuple[str, str, str, bool]:
    if data.startswith(b"MZ"):
        return "application/x-dosexec", "executable", "pe", True
    if data.startswith(b"\x7fELF"):
        return "application/x-executable", "executable", "elf", True
    if data.startswith(b"#!"):
        return "application/x-executable", "executable", "script", True
    if data[:4] in {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
    }:
        return "application/x-executable", "executable", "mach-o", True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav", "audio", "wav", True
    if data.startswith(b"OggS"):
        return "audio/ogg", "audio", "ogg", True
    if data.startswith(b"fLaC"):
        return "audio/flac", "audio", "flac", True
    if data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg", "audio", "mp3", True
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4", "audio", "iso-bmff", True
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm", "audio", "ebml", True
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image", "jpeg", True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image", "png", True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "image", "gif", True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "image", "webp", True
    if data.startswith(b"BM"):
        return "image/bmp", "image", "bmp", True
    if data.startswith(b"%PDF-"):
        return "application/pdf", "document", "pdf", True
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "application/zip", "archive", "zip", True
    try:
        data.decode("utf-8")
        controls = sum(
            byte < 0x20 and byte not in {0x09, 0x0A, 0x0C, 0x0D} for byte in data
        )
        if data and controls * 100 <= len(data):
            return "text/plain", "document", "text", False
    except UnicodeDecodeError:
        pass
    return "application/octet-stream", "unknown", "unknown", False


def _inspect_python(
    path: str,
    filename: str,
    content_type: str,
    max_bytes: int,
    strict: bool,
) -> dict:
    source = Path(path)
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise MediaTransportError("attachment_not_regular") from exc
    if not source.is_file() or source.is_symlink():
        raise MediaTransportError("attachment_not_regular")
    if metadata.st_size <= 0:
        raise MediaTransportError("attachment_empty")
    if metadata.st_size > max_bytes:
        raise MediaTransportError("attachment_too_large")
    digest = hashlib.sha256()
    signature = bytearray()
    total = 0
    try:
        with source.open("rb", buffering=_BUFFER_BYTES) as handle:
            while chunk := handle.read(_BUFFER_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise MediaTransportError("attachment_too_large")
                digest.update(chunk)
                if len(signature) < _SIGNATURE_BYTES:
                    signature.extend(chunk[: _SIGNATURE_BYTES - len(signature)])
    except MediaTransportError:
        raise
    except OSError as exc:
        raise MediaTransportError("attachment_unreadable") from exc
    if total != metadata.st_size:
        raise MediaTransportError("attachment_changed_during_inspection")
    detected_mime, detected_kind, signature_name, confident = _sniff_signature(
        bytes(signature)
    )
    safe_name = Path(filename or source.name).name
    extension_kind = _suffix_kind(safe_name)
    declared_kind = _mime_kind(content_type)
    if detected_kind == "executable":
        raise MediaTransportError("media_signature_executable")
    expected = declared_kind if declared_kind != "unknown" else extension_kind
    mismatch = (
        confident
        and expected != "unknown"
        and detected_kind not in {"unknown", "archive"}
        and expected != detected_kind
    )
    if strict and mismatch:
        raise MediaTransportError("media_signature_mismatch")
    return {
        "filename": safe_name,
        "size_bytes": total,
        "sha256": digest.hexdigest(),
        "detected_mime": detected_mime,
        "detected_kind": detected_kind,
        "signature": signature_name,
        "signature_confident": confident,
        "declared_kind": declared_kind,
        "extension_kind": extension_kind,
        "mismatch": mismatch,
        "backend": "python",
    }


def _native_error(exc: Exception) -> MediaTransportError:
    code = str(exc).strip() or "media_transport_failed"
    return MediaTransportError(code)


def inspect_attachment(
    path: str,
    filename: str = "",
    content_type: str = "",
    *,
    max_bytes: int | None = None,
    strict: bool = True,
) -> dict:
    """Stream, hash, and signature-check one local attachment."""
    started = time.perf_counter()
    limit = int(
        max_bytes
        if max_bytes is not None
        else os.getenv("TELEGRAM_MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024))
    )
    native = _required_native()
    try:
        if native is None:
            result = _inspect_python(path, filename, content_type, limit, strict)
            backend_metric = "python_operations"
        else:
            try:
                values = native.inspect_media(
                    str(path), str(filename), str(content_type), limit, strict
                )
            except ValueError as exc:
                raise _native_error(exc) from exc
            result = dict(
                zip(
                    (
                        "filename",
                        "size_bytes",
                        "sha256",
                        "detected_mime",
                        "detected_kind",
                        "signature",
                        "signature_confident",
                        "declared_kind",
                        "extension_kind",
                        "mismatch",
                    ),
                    values,
                )
            )
            result["backend"] = "rust"
            backend_metric = "native_operations"
        _record(
            inspections=1,
            bytes_inspected=int(result["size_bytes"]),
            total_latency_ms=(time.perf_counter() - started) * 1000,
            **{backend_metric: 1},
        )
        return result
    except Exception:
        _record(failures=1, total_latency_ms=(time.perf_counter() - started) * 1000)
        raise


def _restricted_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    if environment is not None:
        return {str(key): str(value) for key, value in environment.items()}
    return {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}


async def _supervise_python(
    command: Sequence[str],
    stdin_data: bytes,
    *,
    cwd: str | None,
    environment: Mapping[str, str],
    timeout: float,
    max_stdout_bytes: int,
) -> ProcessResult:
    if not command:
        raise MediaTransportError("media_worker_command_empty")
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *[str(value) for value in command],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        env=dict(environment),
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(stdin_data), timeout=max(0.001, timeout)
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if len(stdout) > max_stdout_bytes:
        raise MediaTransportError("media_worker_stdout_limit")
    return ProcessResult(
        process.returncode or 0,
        stdout,
        (time.perf_counter() - started) * 1000,
        "python",
    )


async def supervise_process(
    command: Sequence[str],
    stdin_data: bytes = b"",
    *,
    cwd: str | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float = 120.0,
    max_stdout_bytes: int = 1024 * 1024,
) -> ProcessResult:
    """Run one internal worker with bounded output, deadline, and cancellation."""
    native = _required_native()
    selected_env = _restricted_environment(environment)
    _record(processes=1)
    if native is None:
        _record(python_operations=1)
        try:
            return await _supervise_python(
                command,
                bytes(stdin_data),
                cwd=cwd,
                environment=selected_env,
                timeout=timeout,
                max_stdout_bytes=max_stdout_bytes,
            )
        except asyncio.TimeoutError:
            _record(timeouts=1, failures=1)
            raise
        except asyncio.CancelledError:
            _record(cancellations=1)
            raise
        except Exception:
            _record(failures=1)
            raise

    token = native.CancellationToken()

    async def invoke():
        return await asyncio.to_thread(
            native.run_supervised,
            [str(value) for value in command],
            bytes(stdin_data),
            cwd=cwd,
            environment=list(selected_env.items()),
            timeout_ms=max(1, int(timeout * 1000)),
            max_stdout_bytes=max(1, int(max_stdout_bytes)),
            cancellation=token,
        )

    task = asyncio.create_task(invoke())
    try:
        values = await asyncio.shield(task)
    except asyncio.CancelledError:
        token.cancel()
        _record(cancellations=1)
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise
    except Exception as exc:
        _record(failures=1)
        raise MediaTransportError(str(exc) or "media_worker_failed") from exc
    return_code, stdout, timed_out, cancelled, truncated, duration_ms = values
    _record(native_operations=1, total_latency_ms=float(duration_ms))
    if cancelled:
        _record(cancellations=1)
        raise asyncio.CancelledError()
    if timed_out:
        _record(timeouts=1, failures=1)
        raise asyncio.TimeoutError()
    if truncated:
        _record(failures=1)
        raise MediaTransportError("media_worker_stdout_limit")
    return ProcessResult(int(return_code), bytes(stdout), float(duration_ms), "rust")


async def stream_process_lines(
    command: Sequence[str],
    stdin_data: bytes = b"",
    *,
    cwd: str | None = None,
    environment: Mapping[str, str] | None = None,
    timeout: float = 120.0,
    max_line_bytes: int = 256 * 1024,
) -> AsyncIterator[bytes]:
    """Yield bounded worker stdout lines while retaining native cancellation."""
    native = _required_native()
    selected_env = _restricted_environment(environment)
    _record(processes=1)
    if native is None:
        _record(python_operations=1)
        if not command:
            raise MediaTransportError("media_worker_command_empty")
        process = await asyncio.create_subprocess_exec(
            *[str(value) for value in command],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=selected_env,
            limit=max(1024, int(max_line_bytes) + 1),
        )
        deadline = time.monotonic() + max(0.001, timeout)
        try:
            process.stdin.write(bytes(stdin_data))
            await process.stdin.drain()
            process.stdin.close()
            while True:
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(),
                        max(0.001, deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    _record(timeouts=1, failures=1)
                    raise
                if not line:
                    break
                if len(line) > max_line_bytes:
                    _record(failures=1)
                    raise MediaTransportError("media_worker_stdout_limit")
                yield line
            await asyncio.wait_for(
                process.wait(), max(0.001, deadline - time.monotonic())
            )
            if process.returncode:
                raise RuntimeError("Media worker failed")
        except asyncio.CancelledError:
            _record(cancellations=1)
            raise
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
        return

    started = time.perf_counter()
    try:
        process = native.StreamingProcess(
            [str(value) for value in command],
            bytes(stdin_data),
            cwd=cwd,
            environment=list(selected_env.items()),
            timeout_ms=max(1, int(timeout * 1000)),
            max_line_bytes=max(1, int(max_line_bytes)),
            queue_capacity=64,
        )
    except Exception as exc:
        _record(failures=1)
        raise MediaTransportError(str(exc) or "media_worker_spawn_failed") from exc
    try:
        while True:
            try:
                line = await asyncio.to_thread(process.read_line)
            except RuntimeError as exc:
                if "media_worker_timeout" in str(exc):
                    _record(timeouts=1, failures=1)
                    raise asyncio.TimeoutError() from exc
                _record(failures=1)
                raise MediaTransportError(str(exc)) from exc
            if line is None:
                break
            yield bytes(line)
        try:
            return_code = await asyncio.to_thread(process.wait)
        except RuntimeError as exc:
            if "media_worker_timeout" in str(exc):
                _record(timeouts=1, failures=1)
                raise asyncio.TimeoutError() from exc
            raise
        if return_code:
            raise RuntimeError("Media worker failed")
    except asyncio.CancelledError:
        _record(cancellations=1)
        raise
    finally:
        process.cancel()
        _record(
            native_operations=1,
            total_latency_ms=(time.perf_counter() - started) * 1000,
        )


def _concatenate_wav_python(
    sources: Sequence[str], destination: str, max_bytes: int
) -> dict:
    if not sources:
        raise MediaTransportError("wav_sources_empty")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".curie_wav_", dir=target.parent)
    os.close(descriptor)
    parameters = None
    frames = 0
    size = 44
    try:
        with wave.open(temporary, "wb") as output:
            for source in sources:
                with wave.open(str(source), "rb") as audio:
                    current = (
                        audio.getnchannels(),
                        audio.getsampwidth(),
                        audio.getframerate(),
                        audio.getcomptype(),
                    )
                    if parameters is None:
                        parameters = current
                        output.setnchannels(current[0])
                        output.setsampwidth(current[1])
                        output.setframerate(current[2])
                        output.setcomptype(current[3], "not compressed")
                    elif current != parameters:
                        raise MediaTransportError("wav_format_mismatch")
                    while data := audio.readframes(32768):
                        size += len(data)
                        if size > max_bytes:
                            raise MediaTransportError("wav_output_too_large")
                        output.writeframesraw(data)
                        frames += len(data) // (current[0] * current[1])
        os.replace(temporary, target)
        return {
            "size_bytes": size,
            "frames": frames,
            "sample_rate": parameters[2],
            "channels": parameters[0],
            "bits_per_sample": parameters[1] * 8,
            "backend": "python",
        }
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def concatenate_pcm_wav(
    sources: Sequence[str],
    destination: str,
    *,
    max_bytes: int | None = None,
) -> dict:
    """Concatenate matching PCM WAV files with constant-memory streaming."""
    started = time.perf_counter()
    limit = int(
        max_bytes
        if max_bytes is not None
        else os.getenv("CURIE_MEDIA_MAX_OUTPUT_BYTES", str(256 * 1024 * 1024))
    )
    native = _required_native()
    try:
        if native is None:
            result = _concatenate_wav_python(sources, destination, limit)
            backend_metric = "python_operations"
        else:
            try:
                values = native.concatenate_pcm_wav(
                    [str(value) for value in sources],
                    str(destination),
                    max_bytes=limit,
                )
            except ValueError as exc:
                raise _native_error(exc) from exc
            result = dict(
                zip(
                    (
                        "size_bytes",
                        "frames",
                        "sample_rate",
                        "channels",
                        "bits_per_sample",
                    ),
                    values,
                )
            )
            result["backend"] = "rust"
            backend_metric = "native_operations"
        _record(
            wav_concatenations=1,
            total_latency_ms=(time.perf_counter() - started) * 1000,
            **{backend_metric: 1},
        )
        return result
    except Exception:
        _record(failures=1, total_latency_ms=(time.perf_counter() - started) * 1000)
        raise


def _python_wav_layout(path: str, max_bytes: int) -> tuple[dict, int, int]:
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise MediaTransportError("wav_not_regular")
    size = source.stat().st_size
    if size < 44 or size > max_bytes:
        raise MediaTransportError("wav_size_invalid")
    wav_format = None
    data = None
    try:
        with source.open("rb") as handle:
            header = handle.read(12)
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                raise MediaTransportError("wav_header_invalid")
            while chunk := handle.read(8):
                if len(chunk) != 8:
                    raise MediaTransportError("wav_chunk_invalid")
                chunk_id, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                offset = handle.tell()
                if offset + length + (length & 1) > size:
                    raise MediaTransportError("wav_chunk_invalid")
                if chunk_id == b"fmt ":
                    raw = handle.read(min(length, 16))
                    if len(raw) < 16:
                        raise MediaTransportError("wav_format_invalid")
                    values = struct.unpack("<HHIIHH", raw)
                    wav_format = {
                        "audio_format": values[0],
                        "channels": values[1],
                        "sample_rate": values[2],
                        "byte_rate": values[3],
                        "block_align": values[4],
                        "bits_per_sample": values[5],
                    }
                elif chunk_id == b"data":
                    data = (offset, length)
                handle.seek(offset + length + (length & 1))
    except MediaTransportError:
        raise
    except OSError as exc:
        raise MediaTransportError("wav_unreadable") from exc
    if not wav_format:
        raise MediaTransportError("wav_format_missing")
    if not data:
        raise MediaTransportError("wav_data_missing")
    if (
        wav_format["audio_format"] not in {1, 3}
        or wav_format["channels"] <= 0
        or wav_format["sample_rate"] <= 0
        or wav_format["block_align"] <= 0
        or wav_format["bits_per_sample"] <= 0
    ):
        raise MediaTransportError("wav_format_unsupported")
    if data[1] <= 0 or data[1] % wav_format["block_align"]:
        raise MediaTransportError("wav_data_invalid")
    return wav_format, data[0], data[1]


def wav_chunk_manifest(
    path: str,
    *,
    max_chunk_ms: int = 1000,
    max_bytes: int | None = None,
) -> dict:
    """Describe aligned PCM byte ranges without loading audio into memory."""
    started = time.perf_counter()
    limit = int(
        max_bytes
        if max_bytes is not None
        else os.getenv("CURIE_MEDIA_MAX_OUTPUT_BYTES", str(256 * 1024 * 1024))
    )
    native = _required_native()
    try:
        if native is None:
            wav_format, data_offset, data_len = _python_wav_layout(path, limit)
            total_frames = data_len // wav_format["block_align"]
            target_frames = max(
                1, wav_format["sample_rate"] * max(1, int(max_chunk_ms)) // 1000
            )
            target_bytes = max(
                wav_format["block_align"],
                target_frames * wav_format["block_align"],
            )
            chunks = []
            consumed = 0
            while consumed < data_len:
                length = min(target_bytes, data_len - consumed)
                frames = length // wav_format["block_align"]
                chunks.append(
                    {
                        "offset": data_offset + consumed,
                        "length": length,
                        "frames": frames,
                        "duration_ms": round(
                            frames / wav_format["sample_rate"] * 1000, 6
                        ),
                    }
                )
                consumed += length
            result = {
                "sample_rate": wav_format["sample_rate"],
                "channels": wav_format["channels"],
                "bits_per_sample": wav_format["bits_per_sample"],
                "total_frames": total_frames,
                "chunks": chunks,
                "backend": "python",
            }
            backend_metric = "python_operations"
        else:
            try:
                sample_rate, channels, bits, frames, raw_chunks = (
                    native.wav_chunk_manifest(
                        str(path),
                        max_chunk_ms=max(1, int(max_chunk_ms)),
                        max_bytes=limit,
                    )
                )
            except ValueError as exc:
                raise _native_error(exc) from exc
            result = {
                "sample_rate": sample_rate,
                "channels": channels,
                "bits_per_sample": bits,
                "total_frames": frames,
                "chunks": [
                    {
                        "offset": offset,
                        "length": length,
                        "frames": chunk_frames,
                        "duration_ms": round(duration_ms, 6),
                    }
                    for offset, length, chunk_frames, duration_ms in raw_chunks
                ],
                "backend": "rust",
            }
            backend_metric = "native_operations"
        _record(
            wav_manifests=1,
            total_latency_ms=(time.perf_counter() - started) * 1000,
            **{backend_metric: 1},
        )
        return result
    except Exception:
        _record(failures=1, total_latency_ms=(time.perf_counter() - started) * 1000)
        raise


async def encode_opus(
    source: str,
    destination: str,
    ffmpeg: str,
    *,
    timeout: float = 120.0,
) -> bool:
    """Encode Telegram-compatible Opus under the shared process supervisor."""
    target = Path(destination)
    target.unlink(missing_ok=True)
    command = [
        str(ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-c:a",
        "libopus",
        str(destination),
    ]
    try:
        result = await supervise_process(
            command, timeout=timeout, max_stdout_bytes=4096
        )
        if result.return_code:
            target.unlink(missing_ok=True)
            return False
        inspected = inspect_attachment(
            str(target),
            target.name,
            "audio/ogg",
            max_bytes=int(
                os.getenv("CURIE_MEDIA_MAX_OUTPUT_BYTES", str(256 * 1024 * 1024))
            ),
            strict=True,
        )
        return inspected["detected_kind"] == "audio" and inspected["size_bytes"] > 0
    except asyncio.CancelledError:
        target.unlink(missing_ok=True)
        raise
    except Exception:
        target.unlink(missing_ok=True)
        return False


__all__ = [
    "MediaTransportError",
    "ProcessResult",
    "concatenate_pcm_wav",
    "encode_opus",
    "inspect_attachment",
    "media_transport_metrics",
    "media_transport_status",
    "stream_process_lines",
    "supervise_process",
    "wav_chunk_manifest",
]
