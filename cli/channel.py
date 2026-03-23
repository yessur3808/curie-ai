# cli/channel.py
"""
Channel management for Curie AI.

Provides:
  curie channel list              – List all configured channels and their status
  curie channel doctor            – Check connectivity for each channel
  curie channel bind-telegram TOKEN  – Store / update TELEGRAM_BOT_TOKEN in .env
  curie channel bind-discord TOKEN   – Store / update DISCORD_BOT_TOKEN in .env
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


def _env_file_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv() -> None:
    env_path = _env_file_path()
    if env_path.exists():
        try:
            from dotenv import load_dotenv  # noqa: PLC0415
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


def _update_env_key(key: str, value: str) -> bool:
    """
    Write or update a single key in the project .env file.
    Returns True on success.
    """
    env_path = _env_file_path()
    lines: list[str] = []
    found = False

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and stripped.startswith(key + "="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"{key}={value}")

    try:
        env_path.write_text("\n".join(lines) + "\n")
        return True
    except OSError as e:
        _p(f"[red]❌ Could not write to {env_path}: {e}[/red]")
        return False


# ─── Channel definitions ─────────────────────────────────────────────────────

_CHANNELS = [
    {
        "name": "telegram",
        "token_env": "TELEGRAM_BOT_TOKEN",
        "run_env": "RUN_TELEGRAM",
        "label": "Telegram",
        "api_url": "https://api.telegram.org",
    },
    {
        "name": "discord",
        "token_env": "DISCORD_BOT_TOKEN",
        "run_env": "RUN_DISCORD",
        "label": "Discord",
        "api_url": "https://discord.com/api/v10/gateway",
    },
    {
        "name": "api",
        "token_env": None,
        "run_env": "RUN_API",
        "label": "REST API",
        "api_url": None,
    },
]


def _channel_status(ch: dict) -> dict:
    """Return a status dict for a single channel config."""
    _load_dotenv()
    token = os.getenv(ch["token_env"]) if ch["token_env"] else None
    run_flag = os.getenv(ch["run_env"], "").lower()
    enabled = run_flag in ("1", "true", "yes") or (ch["name"] == "api" and run_flag not in ("0", "false", "no"))

    if ch["token_env"]:
        configured = bool(token)
        masked_token = (token[:8] + "..." + token[-4:]) if token and len(token) > 12 else ("set" if token else "not set")
    else:
        configured = True  # REST API has no token requirement
        masked_token = "N/A"

    return {
        "name": ch["name"],
        "label": ch["label"],
        "configured": configured,
        "enabled": enabled,
        "token_display": masked_token,
        "token_env": ch["token_env"],
    }


# ─── Public commands ──────────────────────────────────────────────────────────


def cmd_channel_list() -> int:
    """List all channels with their configuration status."""
    _load_dotenv()

    if _RICH:
        table = Table(
            title="Curie AI – Channel Configuration",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Channel", style="bold white", min_width=12)
        table.add_column("Token / Key", min_width=22)
        table.add_column("Enabled", min_width=8)
        table.add_column("Env var", style="dim", min_width=22)

        for ch in _CHANNELS:
            st = _channel_status(ch)
            cfg_icon = "[green]✅[/green]" if st["configured"] else "[red]❌[/red]"
            enabled_icon = "[green]yes[/green]" if st["enabled"] else "[yellow]no[/yellow]"
            token_cell = f"{cfg_icon} {st['token_display']}"
            env_hint = st["token_env"] or "—"
            table.add_row(st["label"], token_cell, enabled_icon, env_hint)

        _console.print(table)
    else:
        print(f"{'Channel':<14} {'Configured':<12} {'Enabled':<8} {'Env var'}")
        print("-" * 56)
        for ch in _CHANNELS:
            st = _channel_status(ch)
            print(f"{st['label']:<14} {'yes' if st['configured'] else 'no':<12} {'yes' if st['enabled'] else 'no':<8} {st['token_env'] or '—'}")

    return 0


def cmd_channel_doctor() -> int:
    """Check connectivity for each configured channel."""
    _load_dotenv()
    import socket  # noqa: PLC0415

    overall_ok = True

    for ch in _CHANNELS:
        st = _channel_status(ch)
        label = st["label"]

        if not st["configured"]:
            _p(f"[yellow]⚠️  {label}:[/yellow] not configured (set {st['token_env']})")
            continue

        api_url = ch.get("api_url")
        if not api_url:
            _p(f"[green]✅ {label}:[/green] local — no connectivity check needed")
            continue

        # Quick DNS + HTTP check
        try:
            from urllib.parse import urlparse  # noqa: PLC0415
            import urllib.request  # noqa: PLC0415
            host = urlparse(api_url).hostname
            socket.setdefaulttimeout(4)
            socket.gethostbyname(host)
            with urllib.request.urlopen(api_url, timeout=4) as resp:
                code = resp.getcode()
            ok = code < 500
            if ok:
                _p(f"[green]✅ {label}:[/green] reachable (HTTP {code})")
            else:
                _p(f"[red]❌ {label}:[/red] HTTP {code}")
                overall_ok = False
        except Exception as e:
            _p(f"[red]❌ {label}:[/red] connectivity check failed – {e}")
            overall_ok = False

    return 0 if overall_ok else 1


def cmd_channel_bind(platform: str, token: str) -> int:
    """Store a bot token for *platform* in the .env file."""
    _load_dotenv()

    platform = platform.lower()
    ch_map = {ch["name"]: ch for ch in _CHANNELS}
    ch = ch_map.get(platform)

    if ch is None or ch["token_env"] is None:
        _p(f"[red]❌ Unknown platform or no token required:[/red] {platform!r}")
        _p(f"   Supported: {', '.join(c['name'] for c in _CHANNELS if c['token_env'])}")
        return 1

    env_key = ch["token_env"]
    run_key = ch["run_env"]

    if _update_env_key(env_key, token) and _update_env_key(run_key, "true"):
        _p(f"[green]✅ {ch['label']} token saved.[/green]")
        _p(f"   Restart Curie to activate: [bold]curie restart[/bold]")
        return 0
    return 1
