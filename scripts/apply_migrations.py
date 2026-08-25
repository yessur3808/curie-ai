"""Apply each PostgreSQL migration exactly once and record its checksum."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

import psycopg2

PG_CONN_INFO = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB", "assistant_db"),
    "user": os.getenv("POSTGRES_USER", "assistant"),
    "password": os.getenv("POSTGRES_PASSWORD", "assistantpass"),
}
_MIGRATION = re.compile(r"^(\d{6})_(.+)\.up\.sql$")


def migration_files(
    migrations_dir: str | Path = "migrations",
) -> list[tuple[int, str, Path]]:
    rows = []
    for path in Path(migrations_dir).glob("*.up.sql"):
        match = _MIGRATION.fullmatch(path.name)
        if not match:
            raise ValueError(f"Invalid migration filename: {path.name}")
        rows.append((int(match.group(1)), match.group(2), path))
    rows.sort()
    versions = [row[0] for row in rows]
    if len(versions) != len(set(versions)):
        raise ValueError("Migration versions must be unique")
    return rows


def apply_migrations(migrations_dir: str | Path = "migrations") -> None:
    with psycopg2.connect(**PG_CONN_INFO) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            cursor.execute("SELECT version, checksum FROM schema_migrations")
            applied = dict(cursor.fetchall())
            for version, name, path in migration_files(migrations_dir):
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise RuntimeError(
                            f"Applied migration {version} checksum changed"
                        )
                    continue
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations(version,name,checksum) VALUES(%s,%s,%s)",
                    (version, name, checksum),
                )
                print(f"Applied {path.name}")
    print("All pending migrations applied.")


if __name__ == "__main__":
    apply_migrations()
