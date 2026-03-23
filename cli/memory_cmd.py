# cli/memory_cmd.py
"""
Memory inspection commands for Curie AI.

Wraps the PostgreSQL user table and MongoDB user_profiles collection to give
the operator a quick view of stored memory.

Commands:
  curie memory list               – list all users and their fact count
  curie memory get KEY            – show a specific fact key for the master user
                                    (pass --user INTERNAL_ID to target someone else)
  curie memory stats              – aggregate statistics (users, sessions, facts)
  curie memory clear-user ID      – clear all session memory for a user (master only)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None


def _p(msg: str) -> None:
    if _RICH and _console:
        _console.print(msg)
    else:
        print(re.sub(r"\[/?[a-zA-Z0-9_ ]+\]", "", msg))


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv  # noqa: PLC0415
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _get_mongo_db():
    """Return the MongoDB database object, or raise RuntimeError."""
    from memory.database import mongo_db  # noqa: PLC0415
    return mongo_db


def _get_pg_conn():
    from memory.database import get_pg_conn  # noqa: PLC0415
    return get_pg_conn


def _master_internal_id() -> Optional[str]:
    return os.getenv("MASTER_USER_ID") or None


# ─── Public commands ──────────────────────────────────────────────────────────


def cmd_memory_list(limit: int = 20) -> int:
    """List users and their stored fact counts."""
    _load_dotenv()

    try:
        mdb = _get_mongo_db()
        get_pg = _get_pg_conn()
    except Exception as e:
        _p(f"[red]❌ Database connection failed:[/red] {e}")
        return 1

    # Fetch user rows from PostgreSQL
    try:
        with get_pg() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT internal_id, secret_username, display_name, created_at "
                "FROM users ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    except Exception as e:
        _p(f"[red]❌ PostgreSQL error:[/red] {e}")
        return 1

    # Count facts per user from MongoDB
    fact_counts: dict[str, int] = {}
    try:
        for doc in mdb.user_profiles.find({}, {"_id": 1, "facts": 1}):
            facts = doc.get("facts") or {}
            fact_counts[str(doc["_id"])] = len(facts)
    except Exception:
        pass

    if not rows:
        _p("[yellow]No users found in the database.[/yellow]")
        return 0

    if _RICH:
        table = Table(
            title="Curie AI – User Memory",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Internal ID", style="dim", min_width=24)
        table.add_column("Username / Display", min_width=24)
        table.add_column("Facts", min_width=6)
        table.add_column("Created", min_width=20)

        for row in rows:
            iid = str(row["internal_id"])
            username = str(row.get("display_name") or row.get("secret_username") or "—")
            facts = str(fact_counts.get(iid, 0))
            created = str(row.get("created_at") or "—")[:19]
            table.add_row(iid, username, facts, created)

        _console.print(table)
    else:
        print(f"{'Internal ID':<36} {'Username':<24} {'Facts':<6} Created")
        print("-" * 80)
        for row in rows:
            iid = str(row["internal_id"])
            username = str(row.get("display_name") or row.get("secret_username") or "—")
            facts = str(fact_counts.get(iid, 0))
            created = str(row.get("created_at") or "—")[:19]
            print(f"{iid:<36} {username:<24} {facts:<6} {created}")

    return 0


def cmd_memory_get(key: str, internal_id: Optional[str] = None) -> int:
    """Print the value of a single memory key for a user."""
    _load_dotenv()

    target_id = internal_id or _master_internal_id()
    if not target_id:
        _p("[red]❌ No target user.[/red]  Pass [bold]--user INTERNAL_ID[/bold] or set [bold]MASTER_USER_ID[/bold].")
        return 1

    try:
        from memory.users import UserManager  # noqa: PLC0415
        profile = UserManager.get_user_profile(target_id)
    except Exception as e:
        _p(f"[red]❌ Could not read user profile:[/red] {e}")
        return 1

    if key not in profile:
        _p(f"[yellow]Key [bold]{key}[/bold] not found[/yellow] in user [dim]{target_id}[/dim]'s memory.")
        _p("  Use [bold]curie memory list[/bold] to browse users.")
        return 1

    value = profile[key]
    _p(f"[bold]{key}[/bold] = {value}")
    return 0


def cmd_memory_stats() -> int:
    """Print aggregate memory statistics."""
    _load_dotenv()

    stats: dict[str, object] = {}

    # PostgreSQL: user count
    try:
        get_pg = _get_pg_conn()
        with get_pg() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM users")
            stats["total_users"] = cur.fetchone()[0]
    except Exception as e:
        stats["total_users"] = f"error ({e})"

    # MongoDB: profiles & facts
    try:
        mdb = _get_mongo_db()
        profiles = list(mdb.user_profiles.find({}, {"facts": 1}))
        total_facts = sum(len(p.get("facts") or {}) for p in profiles)
        stats["user_profiles"] = len(profiles)
        stats["total_facts"] = total_facts
    except Exception as e:
        stats["user_profiles"] = f"error ({e})"
        stats["total_facts"] = "error"

    # MongoDB: sessions
    try:
        from memory.session_store import get_session_manager  # noqa: PLC0415
        sm = get_session_manager()
        # The session collection is accessible via sm._collection
        col = getattr(sm, "_collection", None) or getattr(sm, "collection", None)
        if col is not None:
            stats["session_documents"] = col.count_documents({})
        else:
            stats["session_documents"] = "unavailable"
    except Exception as e:
        stats["session_documents"] = f"error ({e})"

    if _RICH:
        table = Table(
            title="Curie AI – Memory Statistics",
            box=box.ROUNDED,
            show_header=False,
        )
        table.add_column("Metric", style="bold cyan")
        table.add_column("Value")
        for k, v in stats.items():
            table.add_row(k.replace("_", " ").title(), str(v))
        _console.print(table)
    else:
        for k, v in stats.items():
            print(f"  {k:<28} {v}")

    return 0


def cmd_memory_clear_user(internal_id: str) -> int:
    """Clear all session memory for a specific user."""
    _load_dotenv()

    try:
        from memory.session_store import get_session_manager  # noqa: PLC0415
        sm = get_session_manager()
        sm.reset_user_all_channels(internal_id)
        _p(f"[green]✅ Session memory cleared for user:[/green] {internal_id}")
        return 0
    except Exception as e:
        _p(f"[red]❌ Could not clear session memory:[/red] {e}")
        return 1
