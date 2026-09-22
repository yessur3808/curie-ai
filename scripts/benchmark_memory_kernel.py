#!/usr/bin/env python3
"""Content-free repeatable benchmark for Curie's memory ranking kernels."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from statistics import median
import time

from memory.hierarchy import (
    clear_retrieval_cache,
    memory_kernel_status,
    rank_memories,
)


def corpus(size: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    topics = (
        ("favorite_food", "spicy noodles and vegetable curry", "preference"),
        ("memory_project", "bounded native retrieval ranking", "project"),
        ("work_note", "compiler profiling and performance", "biography"),
        ("trip_episode", "spring visit to Paris", "episode"),
        ("home_city", "Hong Kong", "identity"),
        ("reading_note", "science history and robotics", "biography"),
    )
    result = []
    for index in range(size):
        key, value, kind = topics[index % len(topics)]
        observed = now - timedelta(days=index % 900)
        result.append(
            {
                "id": f"benchmark-{index}",
                "owner_id": "benchmark-owner",
                "key": f"{key}_{index}",
                "value": f"{value}; synthetic item {index}",
                "summary": "deterministic benchmark data",
                "tags": [key, kind],
                "kind": kind,
                "status": "recorded" if kind == "episode" else "verified",
                "active": True,
                "confidence": 0.8 + (index % 20) / 100,
                "importance": 0.5 + (index % 10) / 20,
                "confirmation_count": index % 8,
                "created_at": observed,
                "last_seen_at": observed,
                "expires_at": None,
            }
        )
    return result


def measure(mode: str, memories: list[dict], iterations: int) -> dict:
    os.environ["CURIE_MEMORY_KERNEL"] = mode
    clear_retrieval_cache()
    queries = (
        "What food do I enjoy?",
        "Recall our native memory retrieval project",
        "What was my Paris trip?",
        "compiler work performance",
    )
    # One cold traversal and one warm traversal are reported separately.
    started = time.perf_counter()
    for query in queries:
        rank_memories(
            query,
            memories,
            owner_id="benchmark-owner",
            explicit_search=True,
        )
    cold_ms = (time.perf_counter() - started) * 1000
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        for query in queries:
            rank_memories(
                query,
                memories,
                owner_id="benchmark-owner",
                explicit_search=True,
            )
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "mode": mode,
        "kernel": memory_kernel_status(),
        "memories": len(memories),
        "queries_per_sample": len(queries),
        "cold_ms": round(cold_ms, 3),
        "warm_median_ms": round(median(samples), 3),
        "warm_queries_per_second": round(len(queries) / (median(samples) / 1000), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="1000,10000")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--mode", choices=("both", "python", "rust"), default="both")
    args = parser.parse_args()
    sizes = [max(1, int(value)) for value in args.sizes.split(",")]
    modes = ("python", "rust") if args.mode == "both" else (args.mode,)
    results = []
    for size in sizes:
        memories = corpus(size)
        for mode in modes:
            results.append(measure(mode, memories, max(1, args.iterations)))
    for size in sizes:
        measurements = {
            item["mode"]: item for item in results if item["memories"] == size
        }
        if set(measurements) == {"python", "rust"}:
            measurements["rust"]["warm_speedup"] = round(
                measurements["python"]["warm_median_ms"]
                / max(0.001, measurements["rust"]["warm_median_ms"]),
                2,
            )
    print(json.dumps({"schema_version": 1, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
