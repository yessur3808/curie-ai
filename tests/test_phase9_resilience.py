import asyncio
from pathlib import Path
import sqlite3

import pytest

from memory import local_store
from memory.backup import create_backup, restore_backup
from services.runtime_health import capability_health, handle_health_command

pytestmark = pytest.mark.integration


def test_sqlite_backup_restore_and_corruption_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    local_store.add_message("telegram", "owner", "user", "before backup")
    backup_path = tmp_path / "backups" / "memory.sqlite3"
    backup = create_backup(backup_path)
    assert Path(backup["manifest"]).is_file()
    assert oct(backup_path.stat().st_mode & 0o777) == "0o600"

    local_store.add_message("telegram", "owner", "assistant", "after backup")
    restored = restore_backup(backup_path, expected_sha256=backup["sha256"])
    history = local_store.get_history("telegram", "owner")
    assert [item["content"] for item in history] == ["before backup"]
    assert Path(restored["rollback"]).is_file()

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        restore_backup(corrupt)


def test_capability_health_publishes_independent_degradation(monkeypatch):
    monkeypatch.setenv("VISION_MODEL_PATH", "/missing/model.gguf")
    monkeypatch.setenv("VISION_MMPROJ_PATH", "/missing/projector.gguf")
    health = capability_health(workflow_ready=False)
    assert health["status"] == "degraded"
    assert health["capabilities"]["text"]["ready"] is False
    assert health["capabilities"]["vision"]["ready"] is False
    assert health["capabilities"]["database"]["ready"] is True
    assert "text=degraded" in handle_health_command("/health", workflow_ready=False)


@pytest.mark.asyncio
async def test_media_backpressure_rejects_without_waiting(monkeypatch):
    from services import media_ingestion

    class Full:
        def acquire(self, blocking=False):
            assert blocking is False
            return False

        def release(self):
            raise AssertionError("unacquired slot must not be released")

    monkeypatch.setattr(media_ingestion, "_media_slots", Full())
    with pytest.raises(RuntimeError, match="Media processing is busy"):
        await media_ingestion.prepare_attachment_message("unused", "note.txt")
