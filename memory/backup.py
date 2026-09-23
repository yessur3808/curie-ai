"""Validated local SQLite backup, restore, and corruption recovery."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


def _validate_database(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    if result != "ok":
        raise ValueError(f"Database integrity check failed: {result}")
    required = {"users", "messages", "profiles", "action_audit"}
    if not required <= tables:
        raise ValueError("Backup does not contain the expected Curie schema")


def create_backup(destination: str | Path) -> dict:
    from memory import local_store

    source = Path(local_store._PATH)
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination:
        raise ValueError("Backup destination must differ from the live database")
    fd, temporary = tempfile.mkstemp(
        prefix="curie_backup_", suffix=".sqlite3", dir=destination.parent
    )
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        with sqlite3.connect(source) as live, sqlite3.connect(temporary_path) as copy:
            live.backup(copy)
        _validate_database(temporary_path)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "bytes": destination.stat().st_size,
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return {**manifest, "path": str(destination), "manifest": str(manifest_path)}


def verify_backup(source: str | Path, expected_sha256: str | None = None) -> dict:
    source = Path(source).resolve()
    _validate_database(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Backup checksum does not match the expected value")
    return {
        "valid": True,
        "path": str(source),
        "sha256": digest,
        "bytes": source.stat().st_size,
    }


def restore_backup(source: str | Path, *, expected_sha256: str | None = None) -> dict:
    """Restore only an explicitly named validated backup, retaining rollback copy."""
    from memory import local_store

    source = Path(source).resolve()
    live = Path(local_store._PATH).resolve()
    verified = verify_backup(source, expected_sha256)
    digest = verified["sha256"]
    try:
        from services.runtime_kernel import reset_persistence

        reset_persistence(live)
        local_store._NATIVE_SCHEMA_READY.discard(str(live))
    except Exception:
        pass
    rollback = live.with_name(
        f"{live.name}.pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    if live.exists():
        shutil.copy2(live, rollback)
        os.chmod(rollback, 0o600)
    fd, temporary = tempfile.mkstemp(
        prefix="curie_restore_", suffix=".sqlite3", dir=live.parent
    )
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        shutil.copy2(source, temporary_path)
        _validate_database(temporary_path)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, live)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {
        "restored": str(live),
        "sha256": digest,
        "rollback": str(rollback) if rollback.exists() else None,
    }
