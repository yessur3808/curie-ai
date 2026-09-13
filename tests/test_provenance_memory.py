from datetime import datetime, timezone
import json

import pytest

from memory import adaptive, local_store

pytestmark = pytest.mark.security


def test_typed_memory_records_provenance_and_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    records = adaptive.record_memories(
        "owner-a",
        {"jacket_preference": "light waterproof jacket"},
        "I prefer a light waterproof jacket",
        source_message_id="msg-17",
        source_channel="telegram",
    )
    record = records[0]
    assert record["owner_id"] == "owner-a"
    assert record["kind"] == "preference"
    assert record["source_message_id"] == "msg-17"
    assert record["source_channel"] == "telegram"
    assert record["status"] == "verified"
    assert adaptive.get_relevant_memories("owner-b", "waterproof jacket") == []


def test_contradiction_waits_for_owner_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    old = adaptive.record_memories("u1", {"favorite_drink": "tea"}, "I prefer tea")[0]
    new = adaptive.record_memories(
        "u1", {"favorite_drink": "coffee"}, "I prefer coffee"
    )[0]
    assert new["active"] is False
    assert new["status"] == "pending_confirmation"
    assert old["id"] in new["contradicts"]
    assert adaptive.get_relevant_memories("u1", "favorite drink")[0]["value"] == "tea"
    assert local_store.get_profile("u1")["favorite_drink"] == "tea"
    response = adaptive.handle_adaptive_command("u1", f"/memory confirm {new['id']}")
    assert response.startswith("Confirmed")
    assert (
        adaptive.get_relevant_memories("u1", "favorite drink")[0]["value"] == "coffee"
    )
    assert local_store.get_profile("u1")["favorite_drink"] == "coffee"


def test_sensitive_and_opt_out_turns_are_not_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    assert (
        adaptive.record_memories(
            "u1", {"password": "secret123"}, "my password is secret123"
        )
        == []
    )
    assert (
        adaptive.record_memories(
            "u1", {"favorite_color": "blue"}, "I like blue, but do not remember this"
        )
        == []
    )
    assert local_store.list_adaptive_memories("u1") == []


def test_hypotheses_are_labelled_and_temporary_context_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    hypothesis = adaptive.record_memories(
        "u1",
        {"possible_interest": "astronomy"},
        "Repeated astronomy questions",
        source="inference",
    )[0]
    temporary = adaptive.record_memories(
        "u1", {"current_trip": "Paris"}, "I am visiting Paris", source_channel="chat"
    )[0]
    assert hypothesis["kind"] == "hypothesis"
    assert hypothesis["status"] == "hypothesis"
    assert hypothesis["expires_at"] is not None
    assert temporary["kind"] == "temporary_context"
    assert datetime.fromisoformat(str(temporary["expires_at"])) > datetime.now(
        timezone.utc
    )


def test_memory_controls_inspect_correct_export_forget_and_disable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    adaptive.record_memories(
        "u1", {"favorite_drink": "tea"}, "I prefer tea", source_channel="api"
    )
    assert "favorite_drink" in adaptive.handle_adaptive_command("u1", "/memory inspect")
    assert "because of" in adaptive.handle_adaptive_command(
        "u1", "/memory why favorite_drink"
    )
    assert "Corrected" in adaptive.handle_adaptive_command(
        "u1", "/memory correct favorite_drink = coffee"
    )
    exported = json.loads(adaptive.handle_adaptive_command("u1", "/memory export"))
    assert exported["owner_id"] == "u1"
    assert all(item["owner_id"] == "u1" for item in exported["memories"])
    assert "disabled" in adaptive.handle_adaptive_command("u1", "/memory pause")
    assert adaptive.get_relevant_memories("u1", "coffee") == []
    assert adaptive.record_memories("u1", {"hobby": "cycling"}, "I enjoy cycling") == []
    assert "enabled" in adaptive.handle_adaptive_command("u1", "/memory resume")
    assert "Forgot" in adaptive.handle_adaptive_command(
        "u1", "/memory forget favorite_drink"
    )


def test_local_semantic_ranking_prefers_related_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    adaptive.record_memories(
        "u1", {"favorite_food": "spicy noodles"}, "I love spicy noodles"
    )
    adaptive.record_memories(
        "u1",
        {"work_project": "compiler optimization"},
        "My project optimizes compilers",
    )
    result = adaptive.get_relevant_memories("u1", "What food do I enjoy?", limit=1)
    assert result[0]["key"] == "favorite_food"


def test_natural_controls_channel_pause_timeline_and_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    adaptive.record_memories("u1", {"drink": "tea"}, "I drink tea", source_channel="telegram")
    assert "timeline" in adaptive.handle_adaptive_command("u1", "/memory timeline").casefold()
    adaptive.handle_adaptive_command("u1", "/memory correct drink = coffee")
    assert "tea" in adaptive.handle_adaptive_command("u1", "/memory rollback drink")
    assert "won’t learn" in adaptive.handle_adaptive_command(
        "u1", "do not learn from this chat", "telegram"
    )
    assert adaptive.record_memories(
        "u1", {"hobby": "cycling"}, "I cycle", source_channel="telegram"
    ) == []


def test_memory_stats_search_and_safe_ambiguous_forget(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    adaptive.record_memories(
        "u1", {"favorite_drink": "coffee"}, "I prefer coffee"
    )

    stats = adaptive.handle_adaptive_command("u1", "/memory stats")
    search = adaptive.handle_adaptive_command("u1", "/memory search coffee")

    assert "1 active" in stats
    assert "1 core" in stats
    assert "favorite_drink" in search
    assert "favorite_drink" in adaptive.handle_adaptive_command(
        "u1", "what do you remember about me?"
    )
    assert "which memory" in adaptive.handle_adaptive_command(
        "u1", "forget that"
    )


def test_forget_removes_core_fact_but_preserves_runtime_controls(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    local_store.update_profile(
        "u1",
        {"proactive_messaging_enabled": False, "legacy_hobby": "cycling"},
    )
    adaptive.record_memories(
        "u1", {"favorite_drink": "tea"}, "I prefer tea"
    )

    adaptive.handle_adaptive_command("u1", "/memory forget all")
    profile = local_store.get_profile("u1")

    assert "favorite_drink" not in profile
    assert "legacy_hobby" not in profile
    assert profile["proactive_messaging_enabled"] is False
