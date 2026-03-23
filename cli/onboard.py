# cli/onboard.py
"""
Guided first-time setup wizard for Curie AI.

Walks the user through configuring the required and optional environment
variables and writes them to the project's .env file.  Ends with a
``curie doctor`` pass so the user sees the final health state.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich import box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None


def _print(msg: str, style: str = "") -> None:
    if _RICH and _console:
        _console.print(msg)
    else:
        import re
        print(re.sub(r"\[/?[a-zA-Z0-9_ ]+\]", "", msg))


def _prompt(label: str, default: str = "", password: bool = False) -> str:
    if _RICH:
        return Prompt.ask(label, default=default, password=password)
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def _confirm(label: str, default: bool = True) -> bool:
    if _RICH:
        return Confirm.ask(label, default=default)
    raw = input(f"{label} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _env_file_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse an existing .env file into a dict."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    """Merge *values* into the .env file, updating existing keys in-place."""
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text().splitlines()

    written_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in values:
                new_lines.append(f'{key}={values[key]}')
                written_keys.add(key)
                continue
        new_lines.append(line)

    # Append new keys not already present
    for key, val in values.items():
        if key not in written_keys:
            new_lines.append(f"{key}={val}")

    path.write_text("\n".join(new_lines) + "\n")


def run_onboard(verbose: bool = False) -> int:
    """
    Interactive first-time setup wizard.
    Returns 0 on success, 1 on abort.
    """
    env_path = _env_file_path()

    if _RICH:
        _console.print(Panel(
            "[bold cyan]Welcome to Curie AI Setup[/bold cyan]\n\n"
            "This wizard will walk you through the required configuration.\n"
            f"Settings will be saved to: [bold]{env_path}[/bold]",
            box=box.ROUNDED,
        ))
    else:
        print("=" * 60)
        print("  Welcome to Curie AI Setup")
        print(f"  Settings will be saved to: {env_path}")
        print("=" * 60)

    existing = _load_env_file(env_path)
    updates: dict[str, str] = {}

    # ── Database ──────────────────────────────────────────────────────────
    _print("\n[bold]1. Database[/bold]")
    _print("  Curie needs PostgreSQL for user data and MongoDB for memory/sessions.")

    pg_dsn = _prompt(
        "  [cyan]POSTGRES_DSN[/cyan]",
        default=existing.get("POSTGRES_DSN", "postgresql://user:pass@localhost:5432/curie"),
    )
    if pg_dsn:
        updates["POSTGRES_DSN"] = pg_dsn

    mongo_uri = _prompt(
        "  [cyan]MONGODB_URI[/cyan]",
        default=existing.get("MONGODB_URI", "mongodb://localhost:27017"),
    )
    if mongo_uri:
        updates["MONGODB_URI"] = mongo_uri

    mongo_db_name = _prompt(
        "  [cyan]MONGODB_DB[/cyan]  (database name)",
        default=existing.get("MONGODB_DB", "curie"),
    )
    if mongo_db_name:
        updates["MONGODB_DB"] = mongo_db_name

    # ── LLM Provider ──────────────────────────────────────────────────────
    _print("\n[bold]2. LLM Provider[/bold]")
    _print("  Choose which AI model provider to use.")

    _providers = ["llama.cpp (local)", "openai", "anthropic", "gemini", "custom (enter manually)"]
    _provider_values = ["llama.cpp", "openai", "anthropic", "gemini", None]
    _existing_priority = existing.get("LLM_PROVIDER_PRIORITY", "llama.cpp")
    # Determine default index from existing value
    _default_idx = 0
    for _i, _v in enumerate(_provider_values):
        if _v and _v in _existing_priority:
            _default_idx = _i
            break

    try:
        from cli.ui import select as _ui_select  # noqa: PLC0415
        _chosen_idx = _ui_select(_providers, title="Primary LLM provider", default=_default_idx)
        _chosen_value = _provider_values[_chosen_idx]
    except (ImportError, Exception):
        _chosen_value = None

    if _chosen_value is not None:
        llm_priority = _chosen_value
    else:
        # "custom" or fallback: ask for free-form input
        llm_priority = _prompt(
            "  [cyan]LLM_PROVIDER_PRIORITY[/cyan]  (comma-separated priority list)",
            default=_existing_priority,
        )
    if llm_priority:
        updates["LLM_PROVIDER_PRIORITY"] = llm_priority

    # Cloud provider keys (only ask if provider is in priority list)
    providers = [p.strip().lower() for p in llm_priority.split(",")]

    if "openai" in providers:
        key = _prompt(
            "  [cyan]OPENAI_API_KEY[/cyan]",
            default=existing.get("OPENAI_API_KEY", ""),
            password=True,
        )
        if key:
            updates["OPENAI_API_KEY"] = key

    if "anthropic" in providers:
        key = _prompt(
            "  [cyan]ANTHROPIC_API_KEY[/cyan]",
            default=existing.get("ANTHROPIC_API_KEY", ""),
            password=True,
        )
        if key:
            updates["ANTHROPIC_API_KEY"] = key

    if "gemini" in providers:
        key = _prompt(
            "  [cyan]GOOGLE_API_KEY[/cyan]",
            default=existing.get("GOOGLE_API_KEY", ""),
            password=True,
        )
        if key:
            updates["GOOGLE_API_KEY"] = key

    # ── Connectors ────────────────────────────────────────────────────────
    _print("\n[bold]3. Connectors[/bold]")
    _print("  Configure chat platform connectors (all are optional).")

    # Interactive multi-select for connectors
    _conn_opts = ["Telegram bot", "Discord bot", "REST API"]
    _conn_defaults: list[int] = []
    if existing.get("TELEGRAM_BOT_TOKEN"):
        _conn_defaults.append(0)
    if existing.get("DISCORD_BOT_TOKEN"):
        _conn_defaults.append(1)
    if existing.get("RUN_API", "true").lower() != "false":
        _conn_defaults.append(2)

    try:
        from cli.ui import multi_select as _ui_multi  # noqa: PLC0415
        _conn_chosen = _ui_multi(_conn_opts, title="Enable connectors (Space to toggle)", defaults=_conn_defaults)
    except (ImportError, Exception):
        _conn_chosen = _conn_defaults

    _want_telegram = 0 in _conn_chosen
    _want_discord = 1 in _conn_chosen
    _want_api = 2 in _conn_chosen

    if _want_telegram:
        token = _prompt(
            "  [cyan]TELEGRAM_BOT_TOKEN[/cyan]",
            default=existing.get("TELEGRAM_BOT_TOKEN", ""),
            password=True,
        )
        if token:
            updates["TELEGRAM_BOT_TOKEN"] = token
            updates["RUN_TELEGRAM"] = "true"

    if _want_discord:
        token = _prompt(
            "  [cyan]DISCORD_BOT_TOKEN[/cyan]",
            default=existing.get("DISCORD_BOT_TOKEN", ""),
            password=True,
        )
        if token:
            updates["DISCORD_BOT_TOKEN"] = token
            updates["RUN_DISCORD"] = "true"

    updates["RUN_API"] = "true" if _want_api else "false"

    # ── Master User ────────────────────────────────────────────────────────
    _print("\n[bold]4. Master User[/bold]")
    _print("  The master user has admin access (start/stop daemon, clear all memory, etc.).")
    master_id = _prompt(
        "  [cyan]MASTER_USER_ID[/cyan]  (internal UUID or leave blank)",
        default=existing.get("MASTER_USER_ID", ""),
    )
    if master_id:
        updates["MASTER_USER_ID"] = master_id

    # ── Confirm & write ───────────────────────────────────────────────────
    _print("\n[bold]5. Summary[/bold]")
    if updates:
        _print(f"  Writing [green]{len(updates)}[/green] settings to [bold]{env_path}[/bold] …")
        _write_env_file(env_path, updates)
        _print("  [green]✅ Configuration saved.[/green]")
    else:
        _print("  [yellow]No changes made.[/yellow]")

    # ── Run doctor ────────────────────────────────────────────────────────
    if _confirm("\nRun [cyan]curie doctor[/cyan] to verify your setup?", default=True):
        from cli.doctor import run_doctor  # noqa: PLC0415
        _print("")
        run_doctor(verbose=verbose)

    return 0
