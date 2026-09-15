"""Versioned forward/rollback migrations for Curie's local SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    up: tuple[str, ...]
    down: tuple[str, ...]


MIGRATIONS = (
    Migration(
        1,
        "owner_time_indexes",
        (
            "CREATE INDEX IF NOT EXISTS idx_messages_owner_time ON messages(internal_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_audit_owner_time ON action_audit(internal_id, created_at)",
        ),
        (
            "DROP INDEX IF EXISTS idx_audit_owner_time",
            "DROP INDEX IF EXISTS idx_messages_owner_time",
        ),
    ),
    Migration(
        2,
        "adaptive_memory_owner_index",
        (
            "CREATE INDEX IF NOT EXISTS idx_adaptive_memories_owner "
            "ON adaptive_memories(internal_id)",
        ),
        ("DROP INDEX IF EXISTS idx_adaptive_memories_owner",),
    ),
    Migration(
        3,
        "session_working_context",
        (
            "CREATE TABLE IF NOT EXISTS session_metadata ("
            "platform TEXT NOT NULL, internal_id TEXT NOT NULL, key TEXT NOT NULL, "
            "value_json TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "PRIMARY KEY(platform, internal_id, key))",
            "CREATE INDEX IF NOT EXISTS idx_session_metadata_owner "
            "ON session_metadata(internal_id, platform, updated_at)",
        ),
        (
            "DROP INDEX IF EXISTS idx_session_metadata_owner",
            "DROP TABLE IF EXISTS session_metadata",
        ),
    ),
)


def current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()
    return int(row[0])


def apply_migrations(connection: sqlite3.Connection, target: int | None = None) -> int:
    target = target if target is not None else MIGRATIONS[-1].version
    known = {migration.version for migration in MIGRATIONS}
    if target < 0 or (target and target not in known):
        raise ValueError("Unknown SQLite schema target")
    version = current_version(connection)
    if target < version:
        return rollback_migrations(connection, target)
    for migration in MIGRATIONS:
        if version < migration.version <= target:
            for statement in migration.up:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES(?, ?, CURRENT_TIMESTAMP)",
                (migration.version, migration.name),
            )
    return current_version(connection)


def rollback_migrations(connection: sqlite3.Connection, target: int) -> int:
    if target < 0:
        raise ValueError("Schema target cannot be negative")
    version = current_version(connection)
    for migration in reversed(MIGRATIONS):
        if target < migration.version <= version:
            for statement in migration.down:
                connection.execute(statement)
            connection.execute(
                "DELETE FROM schema_migrations WHERE version=?", (migration.version,)
            )
    return current_version(connection)
