from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from memory import adaptive, local_store
from memory.hierarchy import (
    clear_retrieval_cache,
    memory_stats,
    rank_memories,
    retrieval_metrics,
)
from memory.learning import learn_from_exchange


def _memory(key, value, *, kind="preference", status="verified", **overrides):
    now = datetime.now(timezone.utc)
    document = {
        "id": key,
        "key": key,
        "value": value,
        "kind": kind,
        "status": status,
        "active": True,
        "confidence": 1.0,
        "confirmation_count": 1,
        "created_at": now,
        "last_seen_at": now,
        "expires_at": None,
    }
    document.update(overrides)
    return document


def test_hybrid_recall_returns_relevant_memory_and_rejects_noise():
    memories = [
        _memory("favorite_food", "spicy noodles"),
        _memory("work_project", "compiler optimization", kind="project"),
        _memory("favorite_jacket", "light waterproof shell"),
    ]

    results = rank_memories("What food do I enjoy?", memories)

    assert [item["key"] for item in results] == ["favorite_food"]
    assert results[0]["_memory_tier"] == "core"
    assert results[0]["_relevance"] >= 0.28


def test_operational_commands_do_not_page_in_old_topics():
    memories = [
        _memory(
            "episode_dreamview_project",
            "We discussed improving the DreamView integration",
            kind="episode",
            status="recorded",
        )
    ]

    assert rank_memories("Turn off the DreamView", memories) == []
    recalled = rank_memories("Do you remember the DreamView project?", memories)
    assert recalled[0]["key"] == "episode_dreamview_project"


def test_expired_pending_and_weak_hypothesis_memories_are_not_recalled():
    now = datetime.now(timezone.utc)
    memories = [
        _memory(
            "old_trip",
            "Paris",
            kind="temporary_context",
            expires_at=now - timedelta(days=1),
        ),
        _memory(
            "pending_trip",
            "Rome",
            status="pending_confirmation",
        ),
        _memory(
            "possible_trip",
            "Berlin",
            kind="hypothesis",
            status="hypothesis",
            confidence=0.5,
        ),
    ]

    assert rank_memories("What was my trip?", memories) == []


def test_recall_deduplicates_keys_and_honors_context_budget():
    memories = [
        _memory("favorite_food", "spicy noodles", confirmation_count=1),
        _memory("favorite_food", "spicy noodles", confirmation_count=4),
        _memory("food_notes", "x" * 600, kind="episode", status="recorded"),
    ]

    results = rank_memories(
        "favorite food and food notes", memories, char_budget=300, limit=8
    )

    assert [item["key"] for item in results] == ["favorite_food"]
    assert results[0]["confirmation_count"] == 4


def test_memory_stats_report_hierarchical_tiers_without_content():
    memories = [
        _memory("name", "Alice", kind="identity"),
        _memory("decision", "Use SQLite", kind="episode", status="recorded"),
        _memory("project", "Curie", kind="project"),
    ]

    stats = memory_stats(memories)

    assert stats["active"] == 3
    assert stats["tiers"] == {"core": 1, "episodic": 1, "archival": 1}


def test_retrieval_reuses_compiled_features_without_changing_results():
    memories = [
        _memory(f"archive_{index}", f"synthetic note {index}", kind="biography")
        for index in range(40)
    ]
    memories.append(_memory("favorite_food", "spicy noodles"))
    clear_retrieval_cache()
    retrieval_metrics(reset=True)

    cold = rank_memories("What food do I enjoy?", memories)
    cold_metrics = retrieval_metrics(reset=True)
    warm = rank_memories("What food do I enjoy?", memories)
    warm_metrics = retrieval_metrics(reset=True)

    assert [item["key"] for item in warm] == [item["key"] for item in cold]
    assert cold_metrics["cache_misses"] == len(memories)
    assert warm_metrics["cache_hits"] == len(memories)
    assert warm_metrics["candidates_reranked"] < warm_metrics["candidates_scanned"]


def test_retrieval_cache_fingerprint_tracks_memory_edits():
    memory = _memory("favorite_drink", "tea")
    clear_retrieval_cache()
    retrieval_metrics(reset=True)
    assert rank_memories("favorite drink", [memory])[0]["value"] == "tea"

    memory["value"] = "coffee"
    assert rank_memories("favorite drink", [memory])[0]["value"] == "coffee"
    metrics = retrieval_metrics(reset=True)

    assert metrics["cache_misses"] == 2


def test_auto_mode_circuit_breaks_to_python_after_native_failure(monkeypatch):
    from memory import hierarchy

    class BrokenKernel:
        @staticmethod
        def kernel_version():
            return "broken-test-kernel"

        @staticmethod
        def cache_entries():
            return 0

        @staticmethod
        def rank_memories(*_args, **_kwargs):
            raise RuntimeError("synthetic native failure")

    monkeypatch.setenv("CURIE_MEMORY_KERNEL", "auto")
    monkeypatch.setattr(hierarchy, "_NATIVE_IMPORT_ATTEMPTED", True)
    monkeypatch.setattr(hierarchy, "_NATIVE_MODULE", BrokenKernel())
    monkeypatch.setattr(hierarchy, "_NATIVE_IMPORT_ERROR", None)
    monkeypatch.setattr(hierarchy, "_NATIVE_FAILURE_LOGGED", False)
    hierarchy.retrieval_metrics(reset=True)

    results = hierarchy.rank_memories(
        "favorite food", [_memory("favorite_food", "spicy noodles")]
    )
    metrics = hierarchy.retrieval_metrics(reset=True)

    assert results[0]["value"] == "spicy noodles"
    assert metrics["native_failures"] == 1
    assert metrics["python_queries"] == 1
    assert metrics["kernel"]["active"] == "python"
    assert metrics["kernel"]["import_error"] == "runtime:RuntimeError"


def test_recall_suppresses_duplicate_long_memory_values():
    repeated = "We agreed to make Curie's memory precise, bounded, and private."
    memories = [
        _memory("memory_plan", repeated, kind="project"),
        _memory("memory_decision", repeated, kind="episode", status="recorded"),
    ]

    results = rank_memories("What did we decide about Curie's memory?", memories)

    assert len(results) == 1


def test_local_store_managed_connection_always_closes(monkeypatch):
    monkeypatch.setenv("CURIE_RUNTIME_KERNEL", "python")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(local_store, "_connect", lambda: connection)

    with local_store._managed_connection() as opened:
        assert opened is connection

    connection.close.assert_called_once_with()


def test_salient_episode_capture_is_bounded_private_and_searchable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    episode = adaptive.record_conversation_episode(
        "u1",
        "Could we build a private hierarchical memory system for Curie?",
        source_message_id="m-1",
        source_channel="telegram",
    )

    assert episode is not None
    assert episode["kind"] == "episode"
    assert len(episode["value"]) <= 350
    assert (
        adaptive.get_relevant_memories("u1", "Curie memory system")[0]["id"]
        == episode["id"]
    )
    synthetic_secret = "sk-" + "1234567890" + "abcdefghijklmnop"
    assert (
        adaptive.record_conversation_episode(
            "u1",
            f"Remember this API key {synthetic_secret}",
            source_channel="telegram",
        )
        is None
    )


def test_routine_chat_is_not_promoted_to_episodic_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")

    assert (
        adaptive.record_conversation_episode(
            "u1", "What is the weather today?", source_channel="telegram"
        )
        is None
    )
    assert local_store.list_adaptive_memories("u1") == []


def test_legacy_memories_are_backfilled_into_the_hierarchy_without_value_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    local_store.upsert_adaptive_memory(
        {
            "_id": "legacy-1",
            "id": "legacy-1",
            "internal_id": "u1",
            "key": "favorite_drink",
            "value": "tea",
            "source": "explicit_user_statement",
        }
    )

    normalized = adaptive._all_owner_memories("u1")[0]
    persisted = local_store.list_adaptive_memories("u1")[0]

    assert normalized["value"] == "tea"
    assert normalized["kind"] == "preference"
    assert normalized["tier"] == "core"
    assert normalized["status"] == "verified"
    assert persisted["value"] == "tea"
    assert persisted["kind"] == "preference"
    assert persisted["status"] == "verified"


def test_conversation_learning_checks_episode_capture_before_fact_fast_path():
    with (
        patch("memory.adaptive.propose_learned_ability"),
        patch("memory.adaptive.record_conversation_episode") as capture,
        patch("memory.learning._should_attempt_extraction", return_value=False),
    ):
        learn_from_exchange(
            "u1",
            "Could we build a persistent memory system?",
            "Yes.",
            source_message_id="m-2",
            source_channel="telegram",
        )

    capture.assert_called_once_with(
        "u1",
        "Could we build a persistent memory system?",
        source_message_id="m-2",
        source_channel="telegram",
    )
