from datetime import datetime, timedelta, timezone
import sqlite3
import zipfile

import pytest

from memory import local_store
from services.security import (
    handle_security_command,
    retention_policy,
    scan_attachment,
)

pytestmark = pytest.mark.security


def test_attachment_gate_rejects_executables_and_archive_traversal(tmp_path):
    executable = tmp_path / "invoice.exe"
    executable.write_bytes(b"MZ" + b"0" * 20)
    with pytest.raises(ValueError, match="Executable"):
        scan_attachment(str(executable), executable.name)

    archive = tmp_path / "document.docx"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.xml", "unsafe")
    with pytest.raises(ValueError, match="unsafe path"):
        scan_attachment(str(archive), archive.name)


def test_attachment_gate_reports_structural_scan_when_clamav_absent(
    tmp_path, monkeypatch
):
    source = tmp_path / "note.txt"
    source.write_text("hello")
    monkeypatch.setattr("services.security.shutil.which", lambda _: None)
    result = scan_attachment(str(source), source.name, "text/plain")
    assert result["safe"] is True
    assert result["malware_scanner"] == "structural_only"


def test_retention_cleanup_is_owner_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "retention.sqlite3")
    local_store.add_message("telegram", "old-owner", "user", "old")
    local_store.add_message("telegram", "other-owner", "user", "keep")
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(local_store._PATH) as connection:
        connection.execute(
            "UPDATE messages SET created_at=? WHERE internal_id='old-owner'", (old,)
        )
        connection.execute(
            "UPDATE messages SET created_at=? WHERE internal_id='other-owner'", (old,)
        )
    removed = local_store.purge_expired_records(
        {"messages": 30, "audit": 90, "completed_tasks": 90, "operational_events": 30},
        owner_id="old-owner",
    )
    assert removed["messages"] == 1
    assert len(local_store.get_history("telegram", "other-owner")) == 1


def test_security_and_retention_controls_are_visible(monkeypatch):
    monkeypatch.setenv("CURIE_RETENTION_MESSAGES_DAYS", "14")
    assert retention_policy()["messages"] == 14
    status = handle_security_command("owner", "/security")
    assert "single-use approvals" in status
    assert "messages=14d" in status
