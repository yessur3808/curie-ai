# cli/memory_cmd.py
"""
Memory inspection commands for Curie AI.

Wraps the PostgreSQL user table and MongoDB user_profiles collection to give
the operator a quick view of stored memory.

Commands:
  curie memory list               – list all users and their fact count
  curie memory keys [--user ID]   – list all fact keys for a user (master user by default)
  curie memory get KEY            – show the value of a key (master user by default)
                                    pass --user INTERNAL_ID to target someone else
  curie memory stats              – aggregate statistics (users, sessions, facts)
  curie memory clear-user ID      – clear all session memory for a user (master only)

``curie memory list``
---------------------
Shows one row per user::

    Internal ID                          Username / Display   Facts  Created
    ─────────────────────────────────── ─────────────────── ─────── ────────────────────
    3f2a1b0c-…                          @alice               12      2024-01-15 10:32:00
    9d8e7f6a-…                          @bob                  4      2024-01-20 14:05:11

The *Facts* column shows how many keys are stored.  To see the key names for a
specific user run::

    curie memory keys --user 3f2a1b0c-…

Or for your own (master) account::

    curie memory keys

``curie memory keys``
---------------------
Shows every stored key for a user along with a short preview of its value::

    Key                 Value preview
    ────────────────── ─────────────────────────────────────────────────
    hobby               reading
    favorite_food       sushi
    birth_month         March
    contact_channels    {platform_priority: ['telegram', 'discord'], …}
    timezone            Europe/London

Use ``curie memory get <KEY>`` to retrieve the full value of any key.
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


def _value_preview(value: object, max_len: int = 48) -> str:
    """Return a short human-readable preview of a fact value."""
    if isinstance(value, dict):
        inner = ", ".join(f"{k}: {repr(v)[:20]}" for k, v in list(value.items())[:3])
        suffix = ", …" if len(value) > 3 else ""
        s = "{" + inner + suffix + "}"
    elif isinstance(value, list):
        inner = ", ".join(repr(v)[:20] for v in value[:4])
        suffix = ", …" if len(value) > 4 else ""
        s = "[" + inner + suffix + "]"
    else:
        s = str(value)
    return (s[:max_len] + "…") if len(s) > max_len + 1 else s


# ─── Public commands ──────────────────────────────────────────────────────────


def cmd_memory_list(limit: int = 20) -> int:
    """
    List users and their stored fact counts.

    Shows: Internal ID, Username/Display, Facts (count), Created date.
    Use ``curie memory keys [--user ID]`` to see the actual key names.
    """
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

    # Count facts per user from MongoDB, and cache key names
    fact_counts: dict[str, int] = {}
    fact_keys: dict[str, list[str]] = {}
    try:
        for doc in mdb.user_profiles.find({}, {"_id": 1, "facts": 1}):
            facts = doc.get("facts") or {}
            uid = str(doc["_id"])
            fact_counts[uid] = len(facts)
            fact_keys[uid] = sorted(facts.keys())
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
        table.add_column("Username / Display", min_width=22)
        table.add_column("Facts", min_width=6)
        table.add_column("Memory keys (preview)", min_width=36)
        table.add_column("Created", min_width=18)

        for row in rows:
            iid = str(row["internal_id"])
            username = str(row.get("display_name") or row.get("secret_username") or "—")
            count = fact_counts.get(iid, 0)
            keys = fact_keys.get(iid, [])
            # Show up to 5 key names, then "…+N more"
            if keys:
                preview_keys = ", ".join(keys[:5])
                if len(keys) > 5:
                    preview_keys += f"  [dim]+{len(keys)-5} more[/dim]"
            else:
                preview_keys = "[dim]none[/dim]"
            created = str(row.get("created_at") or "—")[:19]
            table.add_row(iid, username, str(count), preview_keys, created)

        _console.print(table)
        _console.print(
            "\n[dim]To see all keys for a user: [bold]curie memory keys --user <INTERNAL_ID>[/bold][/dim]"
        )
    else:
        print(f"{'Internal ID':<36} {'Username':<22} {'Facts':<6} Keys (preview)")
        print("-" * 90)
        for row in rows:
            iid = str(row["internal_id"])
            username = str(row.get("display_name") or row.get("secret_username") or "—")
            count = fact_counts.get(iid, 0)
            keys = fact_keys.get(iid, [])
            preview = ", ".join(keys[:5]) + ("…" if len(keys) > 5 else "")
            print(f"{iid:<36} {username:<22} {count:<6} {preview}")
        print()
        print("  To see all keys: curie memory keys --user <INTERNAL_ID>")

    return 0


def cmd_memory_keys(internal_id: Optional[str] = None) -> int:
    """
    List all memory key names (and value previews) for a user.

    If *internal_id* is None, the MASTER_USER_ID is used.

    This is the command to run when you want to know what to pass to
    ``curie memory get <KEY>``.
    """
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

    if not profile:
        _p(f"[yellow]No memory keys found[/yellow] for user [dim]{target_id}[/dim].")
        _p("  Facts are stored when a user uses [bold]/remember key value[/bold] in chat.")
        return 0

    if _RICH:
        table = Table(
            title=f"Memory keys – user {target_id[:16]}…",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Key", style="bold", min_width=22)
        table.add_column("Value preview", min_width=50)

        for key in sorted(profile.keys()):
            table.add_row(key, _value_preview(profile[key]))

        _console.print(table)
        _console.print(
            f"\n[dim]To retrieve a full value: [bold]curie memory get <KEY> --user {target_id}[/bold][/dim]"
        )
    else:
        print(f"Memory keys for user {target_id}:")
        print(f"{'Key':<28} Value preview")
        print("-" * 72)
        for key in sorted(profile.keys()):
            print(f"{key:<28} {_value_preview(profile[key])}")
        print()
        print(f"  To retrieve a full value: curie memory get <KEY> --user {target_id}")

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
        if profile:
            available = ", ".join(sorted(profile.keys())[:10])
            _p(f"  Available keys: [bold]{available}[/bold]")
            _p(f"  Or run: [bold]curie memory keys --user {target_id}[/bold]")
        else:
            _p(f"  No keys stored yet. Run: [bold]curie memory keys --user {target_id}[/bold]")
        return 1

    value = profile[key]
    if _RICH:
        import json as _json  # noqa: PLC0415
        if isinstance(value, (dict, list)):
            formatted = _json.dumps(value, indent=2, default=str)
            _console.print(f"[bold]{key}[/bold] =")
            _console.print(formatted)
        else:
            _console.print(f"[bold]{key}[/bold] = {value}")
    else:
        print(f"{key} = {value}")
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


