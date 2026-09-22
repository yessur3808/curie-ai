"""Parity, boundedness, and cancellation contracts for native media transport."""

import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import time
import wave

import pytest

from services.media_transport import (
    MediaTransportError,
    concatenate_pcm_wav,
    inspect_attachment,
    media_transport_metrics,
    media_transport_status,
    supervise_process,
    wav_chunk_manifest,
)
from services.security import scan_attachment


def _write_wav(path: Path, samples: list[int], *, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(
            b"".join(value.to_bytes(2, "little", signed=True) for value in samples)
        )


def _inspect(mode: str, monkeypatch, path: Path, **kwargs) -> dict:
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", mode)
    return inspect_attachment(str(path), path.name, **kwargs)


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
def test_native_inspection_matches_python_and_streams_hash(tmp_path, monkeypatch):
    source = tmp_path / "sample.wav"
    _write_wav(source, [1, -1, 200, -200] * 5000)

    python = _inspect(
        "python", monkeypatch, source, content_type="audio/wav", strict=True
    )
    native = _inspect(
        "rust", monkeypatch, source, content_type="audio/wav", strict=True
    )

    compared = {
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
    }
    assert {key: native[key] for key in compared} == {
        key: python[key] for key in compared
    }
    assert native["backend"] == "rust"
    assert native["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
@pytest.mark.parametrize("mode", ["python", "rust"])
def test_transport_rejects_executable_signature_and_type_mismatch(
    tmp_path, monkeypatch, mode
):
    executable = tmp_path / "harmless.jpg"
    executable.write_bytes(b"MZ" + b"\0" * 200)
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", mode)
    with pytest.raises(MediaTransportError) as rejected:
        inspect_attachment(str(executable), executable.name, "image/jpeg")
    assert rejected.value.code == "media_signature_executable"

    pdf = tmp_path / "also-not-an-image.jpg"
    pdf.write_bytes(b"%PDF-1.7\nbody")
    with pytest.raises(MediaTransportError) as mismatched:
        inspect_attachment(str(pdf), pdf.name, "image/jpeg")
    assert mismatched.value.code == "media_signature_mismatch"


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
@pytest.mark.parametrize("mode", ["python", "rust"])
def test_transport_rejects_symlinks_empty_and_oversized_files(
    tmp_path, monkeypatch, mode
):
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", mode)
    empty = tmp_path / "empty.wav"
    empty.touch()
    with pytest.raises(MediaTransportError) as rejected:
        inspect_attachment(str(empty), empty.name)
    assert rejected.value.code == "attachment_empty"

    source = tmp_path / "source.txt"
    source.write_text("bounded")
    link = tmp_path / "link.txt"
    link.symlink_to(source)
    with pytest.raises(MediaTransportError) as rejected:
        inspect_attachment(str(link), link.name)
    assert rejected.value.code == "attachment_not_regular"

    with pytest.raises(MediaTransportError) as rejected:
        inspect_attachment(str(source), source.name, max_bytes=3)
    assert rejected.value.code == "attachment_too_large"


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
def test_native_wav_concatenation_matches_python_exactly(tmp_path, monkeypatch):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    python_output = tmp_path / "python.wav"
    native_output = tmp_path / "native.wav"
    samples = [10, -10, 20, -20]
    _write_wav(first, samples)
    _write_wav(second, [30, -30])

    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "python")
    python = concatenate_pcm_wav([str(first), str(second)], str(python_output))
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "rust")
    native = concatenate_pcm_wav([str(first), str(second)], str(native_output))

    assert native_output.read_bytes() == python_output.read_bytes()
    assert native["frames"] == python["frames"] == 6
    assert native["sample_rate"] == python["sample_rate"] == 16000
    assert native["backend"] == "rust"
    with wave.open(str(native_output), "rb") as combined:
        assert combined.getnframes() == 6
        assert (
            combined.readframes(6) == first.read_bytes()[44:] + second.read_bytes()[44:]
        )

    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "python")
    python_manifest = wav_chunk_manifest(str(native_output), max_chunk_ms=1)
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "rust")
    native_manifest = wav_chunk_manifest(str(native_output), max_chunk_ms=1)
    assert native_manifest["chunks"] == python_manifest["chunks"]
    assert native_manifest["total_frames"] == 6
    assert native_manifest["backend"] == "rust"


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
@pytest.mark.parametrize("mode", ["python", "rust"])
def test_wav_concatenation_rejects_format_mismatch(tmp_path, monkeypatch, mode):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write_wav(first, [1, 2], rate=16000)
    _write_wav(second, [3, 4], rate=22050)
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", mode)

    with pytest.raises(MediaTransportError) as rejected:
        concatenate_pcm_wav([str(first), str(second)], str(tmp_path / "out.wav"))
    assert rejected.value.code == "wav_format_mismatch"
    assert not (tmp_path / "out.wav").exists()


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
@pytest.mark.parametrize("mode", ["python", "rust"])
def test_process_supervisor_preserves_output_and_enforces_deadline(monkeypatch, mode):
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", mode)
    success = asyncio.run(
        supervise_process(
            [
                sys.executable,
                "-c",
                "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])",
            ],
            b"curie",
            environment={},
            timeout=2,
        )
    )
    assert success.stdout == b"eiruc"
    assert success.return_code == 0
    assert success.backend == mode

    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            supervise_process(
                [sys.executable, "-c", "import time;time.sleep(5)"],
                environment={},
                timeout=0.05,
            )
        )
    assert time.monotonic() - started < 1.5

    with pytest.raises(MediaTransportError) as oversized:
        asyncio.run(
            supervise_process(
                [sys.executable, "-c", "print('x' * 4096)"],
                environment={},
                timeout=2,
                max_stdout_bytes=64,
            )
        )
    assert oversized.value.code == "media_worker_stdout_limit"


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
@pytest.mark.parametrize("mode", ["python", "rust"])
def test_process_cancellation_reaps_child(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", mode)
    pid_path = tmp_path / f"{mode}.pid"

    async def exercise():
        task = asyncio.create_task(
            supervise_process(
                [
                    sys.executable,
                    "-c",
                    "import os,pathlib,time;"
                    f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));"
                    "time.sleep(30)",
                ],
                environment={},
                timeout=60,
            )
        )
        for _ in range(100):
            if pid_path.exists():
                break
            await asyncio.sleep(0.01)
        assert pid_path.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    child_pid = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
def test_security_scan_uses_native_signature_and_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "rust")
    monkeypatch.setattr("services.security.shutil.which", lambda _name: None)
    source = tmp_path / "note.txt"
    source.write_text("Bonjour from Curie")

    result = scan_attachment(str(source), source.name, "text/plain")

    assert result["safe"] is True
    assert result["transport"] == "rust"
    assert result["signature"] == "text"
    assert result["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.skipif(
    importlib.util.find_spec("_curie_media_transport") is None,
    reason="native media transport is not built",
)
def test_signature_classifies_extensionless_media_for_ingestion(tmp_path, monkeypatch):
    from services.media_ingestion import _prepare_attachment_message

    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "rust")
    monkeypatch.setattr("services.security.shutil.which", lambda _name: None)
    source = tmp_path / "telegram_blob"
    source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload")

    async def analyze(_path, _prompt):
        return "A bounded test image"

    monkeypatch.setattr("services.media_ingestion.analyze_image", analyze)
    result = asyncio.run(
        _prepare_attachment_message(str(source), source.name, "describe it")
    )

    assert "LOCAL VISION RESULT" in result
    assert '"kind": "image"' in result


def test_python_rollback_status_and_metrics_are_content_free(tmp_path, monkeypatch):
    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "python")
    source = tmp_path / "note.txt"
    source.write_text("private media text")
    media_transport_metrics(reset=True)

    inspect_attachment(str(source), source.name, "text/plain")
    metrics = media_transport_metrics(reset=True)

    assert media_transport_status()["active"] == "python"
    assert metrics["python_operations"] == 1
    assert metrics["inspections"] == 1
    assert "private media text" not in str(metrics)


def test_required_native_mode_fails_closed_when_extension_is_missing(
    tmp_path, monkeypatch
):
    from services import media_transport

    monkeypatch.setenv("CURIE_MEDIA_TRANSPORT", "rust")
    monkeypatch.setattr(media_transport, "_NATIVE_IMPORT_ATTEMPTED", True)
    monkeypatch.setattr(media_transport, "_NATIVE_MODULE", None)
    monkeypatch.setattr(media_transport, "_NATIVE_IMPORT_ERROR", "ImportError")
    source = tmp_path / "note.txt"
    source.write_text("bounded")

    assert media_transport_status()["active"] == "python"
    with pytest.raises(RuntimeError, match="native module is unavailable"):
        inspect_attachment(str(source), source.name)
