"""Synthetic benchmark for Curie's memory retrieval; never reads stored data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
import statistics
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if (PROJECT_ROOT / "memory").is_dir():
    sys.path.insert(0, str(PROJECT_ROOT))

from memory.hierarchy import (  # noqa: E402 - repository path bootstrap
    clear_retrieval_cache,
    rank_memories,
    retrieval_metrics,
)


def document(index: int, key: str, value: str, kind: str = "biography") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": f"memory-{index}",
        "id": f"memory-{index}",
        "kind": kind,
        "key": key,
        "value": value,
        "confidence": 1.0,
        "importance": 0.75,
        "confirmation_count": 2,
        "status": "verified",
        "active": True,
        "created_at": now - timedelta(days=index % 90),
        "updated_at": now,
    }


def dataset(size: int = 500) -> list[dict]:
    memories = [
        document(0, "favorite_food", "Japanese ramen", "preference"),
        document(1, "current_project", "A compact Rust compiler", "project"),
        document(2, "rain_jacket", "The blue waterproof shell", "preference"),
        document(
            3, "dreamview_plan", "Connect the DreamView lighting system", "episode"
        ),
    ]
    adjectives = ["amber", "quiet", "rapid", "silver", "coastal", "modular"]
    nouns = ["notebook", "garden", "camera", "recipe", "bicycle", "podcast"]
    for index in range(4, size):
        memories.append(
            document(
                index,
                f"archive_{index}_{nouns[index % len(nouns)]}",
                f"Synthetic {adjectives[index % len(adjectives)]} "
                f"{nouns[(index + 2) % len(nouns)]} note number {index}",
                "biography",
            )
        )
    random.Random(42).shuffle(memories)
    return memories


CASES = (
    ("What food do I like?", "favorite_food"),
    ("What project am I building?", "current_project"),
    ("Which waterproof jacket did I mention?", "rain_jacket"),
    ("What do you remember about DreamView?", "dreamview_plan"),
    ("turn off the dreamview", None),
    ("weather on Jupiter", None),
)


def main() -> None:
    memories = dataset()
    clear_retrieval_cache()
    retrieval_metrics(reset=True)
    timings = []
    correct = 0
    repetitions = 12
    for _ in range(repetitions):
        for query, expected in CASES:
            started = time.perf_counter()
            result = rank_memories(query, memories)
            timings.append((time.perf_counter() - started) * 1000)
            keys = [item.get("key") for item in result]
            correct += (
                not keys if expected is None else bool(keys and keys[0] == expected)
            )
    ordered = sorted(timings)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    print(f"documents={len(memories)} queries={len(timings)}")
    print(f"accuracy={correct}/{len(timings)}")
    print(f"mean_ms={statistics.mean(timings):.3f}")
    print(f"median_ms={statistics.median(timings):.3f}")
    print(f"p95_ms={p95:.3f}")
    diagnostics = retrieval_metrics(reset=True)
    print(f"cache_hit_rate={diagnostics['cache_hit_rate']:.4f}")
    print(f"candidates_reranked={diagnostics['candidates_reranked']}")


if __name__ == "__main__":
    main()
