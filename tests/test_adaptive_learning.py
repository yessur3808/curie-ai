from unittest.mock import MagicMock, patch

from memory import adaptive
from memory import local_store


def test_guard_rejects_consequential_predicted_actions():
    assert adaptive.is_safe_proposed_action("Would you like a short study outline?")
    assert not adaptive.is_safe_proposed_action(
        "Should I purchase the tickets for you?"
    )
    assert not adaptive.is_safe_proposed_action(
        "Would you like me to delete those files?"
    )


def test_ability_requires_explicit_teaching_and_stays_pending(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://test")
    fake_db = MagicMock()
    with patch.object(adaptive, "mongo_db", fake_db):
        assert adaptive.propose_learned_ability("u1", "Please help with notes") is None
        proposal = adaptive.propose_learned_ability(
            "u1", "When I say morning brief, summarize my current priorities"
        )
    assert proposal["status"] == "pending"
    assert proposal["requires_confirmation"] is True
    assert proposal["kind"] == "declarative_response"
    assert proposal["version"] == 1
    assert proposal["workflow_steps"] == []
    fake_db.learned_abilities.update_one.assert_called_once()


def test_unsafe_ability_is_not_stored(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://test")
    fake_db = MagicMock()
    with patch.object(adaptive, "mongo_db", fake_db):
        result = adaptive.propose_learned_ability(
            "u1", "When I say cleanup, delete all old files"
        )
    assert result is None
    fake_db.learned_abilities.update_one.assert_not_called()


def test_ability_approval_is_user_scoped(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    adaptive.propose_learned_ability(
        "u1", "When I say morning brief, summarize my current priorities"
    )
    assert "could not find" in adaptive.handle_adaptive_command(
        "u2", "/approve skill morning_brief"
    )
    result = adaptive.handle_adaptive_command("u1", "/approve skill morning_brief")
    assert result == "Learned skill `morning_brief` approved."
    assert adaptive.get_matching_abilities("u2", "morning brief") == []


def test_explicit_memories_have_provenance_and_no_expiry(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://test")
    fake_db = MagicMock()
    with patch.object(adaptive, "mongo_db", fake_db):
        adaptive.record_memories("u1", {"favorite_drink": "tea"}, "I prefer tea")
    update = fake_db.adaptive_memories.update_one.call_args.args[1]
    assert update["$set"]["source"] == "explicit_user_statement"
    assert update["$set"]["confidence"] == 1.0
    assert update["$set"]["expires_at"] is None
    assert update["$inc"]["confirmation_count"] == 1


def test_local_sqlite_fallback_persists_profile_history_and_skill(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    user_id = local_store.get_or_create_user("telegram", "42")
    local_store.update_profile(user_id, {"favorite_drink": "tea"})
    local_store.add_message("telegram", user_id, "user", "Hello")
    proposal = adaptive.propose_learned_ability(
        user_id, "When I say morning brief, summarize my priorities"
    )
    assert local_store.get_profile(user_id)["favorite_drink"] == "tea"
    assert local_store.get_history("telegram", user_id)[0]["content"] == "Hello"
    assert proposal["status"] == "pending"
    assert adaptive.handle_adaptive_command(
        user_id, "/approve skill morning_brief"
    ).endswith("approved.")
