"""Content-safe baseline and microprofile for Curie's native runtime boundary."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import sqlite3
import tempfile
import threading
import time


@contextmanager
def configured_mode(mode: str):
    previous = os.environ.get("CURIE_RUNTIME_KERNEL")
    os.environ["CURIE_RUNTIME_KERNEL"] = mode
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CURIE_RUNTIME_KERNEL", None)
        else:
            os.environ["CURIE_RUNTIME_KERNEL"] = previous


def _elapsed_ms(operation, iterations: int) -> float:
    started = time.perf_counter()
    for index in range(iterations):
        operation(index)
    return round((time.perf_counter() - started) * 1000, 3)


def _rss_bytes() -> int:
    # Linux reports KiB, macOS bytes. This benchmark primarily runs on Linux.
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value * 1024 if os.uname().sysname == "Linux" else value


def profile(mode: str, iterations: int) -> dict:
    from services import runtime_kernel

    runtime_kernel._STORES.clear()
    with tempfile.TemporaryDirectory(prefix="curie-runtime-profile-") as directory:
        path = Path(directory) / "profile.sqlite3"
        with configured_mode(mode):
            if mode == "rust":
                connection = runtime_kernel.persistence_connection(path)
                with connection:
                    connection.execute(
                        "CREATE TABLE samples(id INTEGER PRIMARY KEY,value TEXT)"
                    )

                def persist(index):
                    with connection:
                        connection.execute(
                            "INSERT INTO samples(value) VALUES(?)", (f"value-{index}",)
                        )

            else:
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        "CREATE TABLE samples(id INTEGER PRIMARY KEY,value TEXT)"
                    )

                def persist(index):
                    with sqlite3.connect(path) as connection:
                        connection.execute(
                            "INSERT INTO samples(value) VALUES(?)", (f"value-{index}",)
                        )

            persistence_ms = _elapsed_ms(persist, iterations)

            from services.cron_runner import cron_matches

            when = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
            if mode == "rust":
                import _curie_task_engine as native_task

                epoch = int(when.timestamp() * 1000)
                schedule = lambda _: native_task.cron_matches(  # noqa: E731
                    "*/5 8-18 * * 1-5", epoch
                )
            else:
                schedule = lambda _: cron_matches(  # noqa: E731
                    "*/5 8-18 * * 1-5", when
                )
            scheduler_ms = _elapsed_ms(schedule, iterations * 10)

            text = "Turn off all the living-room lights, please."
            if mode == "rust":
                language = lambda _: runtime_kernel.preprocess_text(text)  # noqa: E731
            else:
                from agent.kernel.inbound import normalize_message_text

                language = lambda _: normalize_message_text(text)  # noqa: E731
            language_ms = _elapsed_ms(language, iterations * 10)

    try:
        from llm import manager

        resident = [
            {
                "model": Path(name).name,
                "file_bytes": (
                    (Path("models") / name).stat().st_size
                    if (Path("models") / name).is_file()
                    else None
                ),
            }
            for name in manager.llama_models_cache
        ]
    except Exception:
        resident = []
    return {
        "mode": mode,
        "iterations": iterations,
        "persistence_total_ms": persistence_ms,
        "persistence_ops_per_second": round(
            iterations / max(0.001, persistence_ms / 1000), 1
        ),
        "scheduler_total_ms": scheduler_ms,
        "language_total_ms": language_ms,
        "process": {
            "pid": os.getpid(),
            "rss_high_water_bytes": _rss_bytes(),
            "threads": threading.active_count(),
        },
        "model_residency": {
            "resident_count": len(resident),
            "models": resident,
            "configured_max": int(os.getenv("LLM_MAX_LOADED_MODELS", "2")),
            "inference_workers": int(os.getenv("INFERENCE_WORKERS", "1")),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("python", "rust", "both"), default="both")
    parser.add_argument("--iterations", type=int, default=500)
    args = parser.parse_args()
    modes = ("python", "rust") if args.mode == "both" else (args.mode,)
    results = [profile(mode, max(1, args.iterations)) for mode in modes]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
