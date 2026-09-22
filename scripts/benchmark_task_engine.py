"""Bounded, content-free benchmark for task graph hashing and validation."""

from __future__ import annotations

import argparse
import json
import os
import time

from agent import task_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--mode", choices=("auto", "rust", "python"), default="auto")
    args = parser.parse_args()
    iterations = max(1, min(1_000_000, args.iterations))
    step_count = max(1, min(1_000, args.steps))
    os.environ["CURIE_TASK_ENGINE"] = args.mode
    graph = [
        {
            "id": f"step-{index}",
            "depends_on": [] if index == 0 else [f"step-{index - 1}"],
        }
        for index in range(step_count)
    ]
    payload = {"owner": "benchmark-owner", "steps": graph}
    started = time.perf_counter()
    digest = ""
    for _ in range(iterations):
        task_engine.validate_task_graph(graph)
        digest = task_engine.hash_payload(payload)
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "backend": task_engine.task_engine_status()["active"],
                "iterations": iterations,
                "steps": step_count,
                "digest_prefix": digest[:12],
                "milliseconds": round(elapsed * 1000, 3),
                "operations_per_second": round(
                    (iterations * 2) / max(elapsed, 1e-9), 1
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
