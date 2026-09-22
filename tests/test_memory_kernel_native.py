"""Parity, isolation, and rollback contracts for Curie's Rust memory kernel."""

from datetime import datetime, timedelta, timezone
import importlib.util
import random

import pytest

from memory.hierarchy import (
    _rank_memories_python,
    clear_retrieval_cache,
    memory_kernel_status,
    rank_memories,
    retrieval_metrics,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("_curie_memory_kernel") is None,
    reason="native memory kernel is not built",
)


def _memory(index: int, *, owner: str = "owner-a", **overrides):
    observed = datetime.now(timezone.utc) - timedelta(days=(index * 7) % 800)
    document = {
        "id": f"memory-{index}",
        "record_id": f"memory-{index}",
        "owner_id": owner,
        "key": f"archive_note_{index}",
        "value": f"A bounded synthetic archive note numbered {index}",
        "summary": "",
        "tags": [],
        "kind": "biography",
        "status": "verified",
        "active": True,
        "confidence": 0.85,
        "importance": 0.6,
        "confirmation_count": index % 5,
        "created_at": observed,
        "last_seen_at": observed,
        "expires_at": None,
    }
    document.update(overrides)
    return document


def _projection(results):
    return [
        (
            item["id"],
            item["_relevance"],
            item["_retrieval_reason"],
            item["_memory_tier"],
        )
        for item in results
    ]


def _both(query, memories, **kwargs):
    clear_retrieval_cache()
    python = _rank_memories_python(query, memories, **kwargs)
    clear_retrieval_cache()
    native = rank_memories(query, memories, **kwargs)
    return _projection(python), _projection(native)


def test_native_kernel_matches_python_for_scoring_deduplication_and_budget(
    monkeypatch,
):
    monkeypatch.setenv("CURIE_MEMORY_KERNEL", "rust")
    memories = [
        _memory(
            1,
            key="favorite_food",
            value="spicy noodles",
            kind="preference",
            confirmation_count=4,
        ),
        _memory(
            2,
            key="favorite_food",
            value="spicy noodles",
            kind="preference",
            confirmation_count=1,
        ),
        _memory(
            3,
            key="food_journal",
            value="A very long unrelated entry " * 30,
            kind="episode",
            status="recorded",
        ),
        _memory(4, key="work_project", value="compiler optimization", kind="project"),
        _memory(
            5,
            key="possible_food",
            value="sushi",
            kind="hypothesis",
            status="hypothesis",
            confidence=0.4,
        ),
    ]

    python, native = _both(
        "What food do I enjoy?",
        memories,
        limit=8,
        char_budget=300,
        owner_id="owner-a",
    )

    assert native == python
    assert native[0][0] == "memory-1"


def test_native_kernel_matches_python_over_deterministic_varied_corpus(monkeypatch):
    monkeypatch.setenv("CURIE_MEMORY_KERNEL", "rust")
    rng = random.Random(852)
    topics = [
        ("favorite_food", "spicy noodles", "preference"),
        ("home_city", "Hong Kong", "identity"),
        ("memory_project", "bounded native retrieval", "project"),
        ("trip_episode", "visited Paris in spring", "episode"),
        ("work_note", "compiler performance analysis", "biography"),
    ]
    memories = []
    for index in range(180):
        key, value, kind = topics[index % len(topics)]
        memories.append(
            _memory(
                index,
                key=f"{key}_{index}",
                value=f"{value} item {rng.randrange(20)}",
                kind=kind,
                status="recorded" if kind == "episode" else "verified",
                confidence=rng.uniform(0.75, 1.0),
                importance=rng.uniform(0.35, 1.0),
                confirmation_count=rng.randrange(8),
            )
        )
    for query in (
        "favorite food",
        "Where is my home city?",
        "What did we discuss about memory retrieval?",
        "Recall my Paris trip",
        "compiler work performance",
        "unrelated telescope calibration",
    ):
        python, native = _both(
            query,
            memories,
            limit=8,
            char_budget=1600,
            explicit_search=True,
            owner_id="owner-a",
        )
        assert native == python, query


@pytest.mark.security
def test_native_kernel_enforces_owner_boundary_again(monkeypatch):
    monkeypatch.setenv("CURIE_MEMORY_KERNEL", "rust")
    memories = [
        _memory(1, owner="owner-a", key="favorite_food", value="noodles"),
        _memory(2, owner="owner-b", key="favorite_food", value="private curry"),
    ]

    results = rank_memories(
        "favorite food",
        memories,
        explicit_search=True,
        owner_id="owner-a",
    )

    assert [item["owner_id"] for item in results] == ["owner-a"]
    assert all("private curry" not in str(item) for item in results)


def test_python_rollback_remains_explicit_and_observable(monkeypatch):
    monkeypatch.setenv("CURIE_MEMORY_KERNEL", "python")
    clear_retrieval_cache()
    retrieval_metrics(reset=True)

    results = rank_memories(
        "favorite food",
        [_memory(1, key="favorite_food", value="noodles")],
        owner_id="owner-a",
    )
    metrics = retrieval_metrics(reset=True)

    assert results[0]["value"] == "noodles"
    assert memory_kernel_status()["active"] == "python"
    assert metrics["python_queries"] == 1
    assert metrics["native_queries"] == 0


def test_native_kernel_cache_metrics_are_exposed(monkeypatch):
    monkeypatch.setenv("CURIE_MEMORY_KERNEL", "rust")
    memories = [_memory(index) for index in range(30)]
    memories.append(_memory(100, key="favorite_food", value="noodles"))
    clear_retrieval_cache()
    retrieval_metrics(reset=True)

    rank_memories("favorite food", memories, owner_id="owner-a")
    cold = retrieval_metrics(reset=True)
    rank_memories("favorite food", memories, owner_id="owner-a")
    warm = retrieval_metrics(reset=True)

    assert cold["cache_misses"] == len(memories)
    assert warm["cache_hits"] == len(memories)
    assert warm["kernel"]["active"] == "rust"
    assert warm["native_queries"] == 1
