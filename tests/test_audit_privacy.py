import json
import os
from pathlib import Path

from memory import local_store
from memory.repositories import get_repositories, reset_repositories
from services.audit import (
    handle_audit_command,
    normalize_audit_details,
    redact,
    security_alerts,
)


def test_recursive_redaction_hides_secrets_contents_and_sensitive_paths(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CURIE_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    safe = redact(
        {
            "token": "abc123",
            "query": "private personal question",
            "path": "/home/person/.ssh/id_ed25519",
            "nested": {"authorization": "Bearer secret"},
        }
    )
    assert safe["token"] == "[REDACTED]"
    assert safe["query"] == {"sha256": safe["query"]["sha256"], "length": 25}
    assert safe["path"] == "[SENSITIVE_PATH]/id_ed25519"
    assert safe["nested"]["authorization"] == "[REDACTED]"


def test_normalized_event_has_reconstruction_fields_without_content():
    event = normalize_audit_details(
        {
            "connector": "telegram",
            "validated_action": "research",
            "params": {"query": "my private plans", "api_key": "secret"},
            "policy_decision": "allowed",
            "approval": {"required": False},
            "tool_version": "2.1",
            "result": "personal response",
            "citations": ["https://user:pass@example.com/path?q=private"],
        }
    )
    assert event["connector"] == "telegram"
    assert event["parameters"]["api_key"] == "[REDACTED]"
    assert event["parameters"]["query"]["length"] == 16
    assert event["outcome"]["length"] == 17
    assert event["citations"] == ["https://example.com/path"]


def test_sqlite_audit_is_chained_private_exportable_and_owner_deletable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "audit.sqlite3")
    reset_repositories()
    repo = get_repositories().audits
    repo.append("u1", "inspect", "completed", {"result": "hello"})
    repo.append(
        "u1", "change", "completed", {"changed_files": [tmp_path / "secret.txt"]}
    )
    repo.append("u2", "inspect", "completed", {})
    events = repo.list("u1", 10)
    assert (
        events[0]["details"]["previous_hash"] == events[1]["details"]["integrity_hash"]
    )
    assert os.stat(local_store._PATH).st_mode & 0o777 == 0o600
    exported = json.loads(handle_audit_command("u1", "/audit export"))
    assert len(exported["events"]) == 2
    assert "irreversible" in handle_audit_command("u1", "/audit delete")
    assert "Deleted 2" in handle_audit_command("u1", "/audit delete confirm")
    assert len(repo.list("u2")) == 1
    reset_repositories()


def test_security_thresholds_are_category_specific():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    events = [
        {"created_at": now, "details": {"security_category": "approval_failure"}}
        for _ in range(5)
    ]
    assert security_alerts(events)[0]["category"] == "approval_failure"
