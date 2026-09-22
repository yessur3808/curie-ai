"""Deterministic, content-free benchmark for Curie's media transport kernels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import tempfile
import time
import wave

from services.media_transport import (
    concatenate_pcm_wav,
    inspect_attachment,
    media_transport_status,
)


def _write_wav(path: Path, payload_bytes: int) -> None:
    frames = max(1, payload_bytes // 2)
    chunk = (b"\x01\x00\xff\xff") * 16384
    remaining = frames * 2
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        while remaining:
            data = chunk[: min(len(chunk), remaining)]
            output.writeframesraw(data)
            remaining -= len(data)


def _measure(mode: str, size_bytes: int, iterations: int, directory: Path) -> dict:
    os.environ["CURIE_MEDIA_TRANSPORT"] = mode
    first = directory / f"{mode}-first.wav"
    second = directory / f"{mode}-second.wav"
    combined = directory / f"{mode}-combined.wav"
    _write_wav(first, size_bytes // 2)
    _write_wav(second, size_bytes // 2)

    inspect_times = []
    concat_times = []
    for _ in range(max(1, iterations)):
        started = time.perf_counter()
        inspect_attachment(
            str(first), first.name, "audio/wav", max_bytes=size_bytes + 1024
        )
        inspect_times.append((time.perf_counter() - started) * 1000)

        combined.unlink(missing_ok=True)
        started = time.perf_counter()
        concatenate_pcm_wav(
            [str(first), str(second)],
            str(combined),
            max_bytes=size_bytes + 4096,
        )
        concat_times.append((time.perf_counter() - started) * 1000)

    return {
        "mode": mode,
        "kernel": media_transport_status(),
        "payload_bytes": size_bytes,
        "iterations": max(1, iterations),
        "inspect_median_ms": round(statistics.median(inspect_times), 3),
        "concat_median_ms": round(statistics.median(concat_times), 3),
        "combined_bytes": combined.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--megabytes", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--mode", choices=("both", "python", "rust"), default="both")
    args = parser.parse_args()
    size_bytes = max(1, args.megabytes) * 1024 * 1024
    modes = ("python", "rust") if args.mode == "both" else (args.mode,)
    with tempfile.TemporaryDirectory(prefix="curie_media_benchmark_") as raw:
        directory = Path(raw)
        results = [
            _measure(mode, size_bytes, args.iterations, directory) for mode in modes
        ]
    if len(results) == 2:
        python, rust = results
        rust["inspect_speedup"] = round(
            python["inspect_median_ms"] / max(0.001, rust["inspect_median_ms"]), 2
        )
        rust["concat_speedup"] = round(
            python["concat_median_ms"] / max(0.001, rust["concat_median_ms"]), 2
        )
    print(json.dumps({"schema_version": 1, "results": results}, indent=2))


if __name__ == "__main__":
    main()
