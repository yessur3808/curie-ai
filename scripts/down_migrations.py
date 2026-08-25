"""Roll PostgreSQL migrations back to an explicit target version."""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg2

from scripts.apply_migrations import PG_CONN_INFO, migration_files


def rollback_to(target: int, migrations_dir: str | Path = "migrations") -> None:
    if target < 0:
        raise ValueError("Target version cannot be negative")
    known = {
        version: (name, path) for version, name, path in migration_files(migrations_dir)
    }
    with psycopg2.connect(**PG_CONN_INFO) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM schema_migrations "
                "WHERE version>%s ORDER BY version DESC",
                (target,),
            )
            for (version,) in cursor.fetchall():
                if version not in known:
                    raise RuntimeError(
                        f"No rollback file is known for migration {version}"
                    )
                _, up_path = known[version]
                down_path = up_path.with_name(
                    up_path.name.replace(".up.sql", ".down.sql")
                )
                if not down_path.is_file():
                    raise RuntimeError(f"Missing rollback migration: {down_path.name}")
                cursor.execute(down_path.read_text(encoding="utf-8"))
                cursor.execute(
                    "DELETE FROM schema_migrations WHERE version=%s", (version,)
                )
                print(f"Rolled back {down_path.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--migrations-dir", default="migrations")
    arguments = parser.parse_args()
    rollback_to(arguments.target, arguments.migrations_dir)
