# cli/channel.py
"""
Channel management for Curie AI.

Provides:
  curie channel list              – List all configured channels with identity & status
  curie channel doctor            – Check connectivity for each channel
  curie channel bind-telegram TOKEN  – Store / update TELEGRAM_BOT_TOKEN in .env
  curie channel bind-discord TOKEN   – Store / update DISCORD_BOT_TOKEN in .env

``curie channel list`` shows, for each connector:
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │ Channel   │ Status      │ Identity          │ Env var             │ How to set up │
  ├───────────┼─────────────┼───────────────────┼─────────────────────┼───────────────┤
  │ Telegram  │ ✅ ready    │ @MyCurieBot       │ TELEGRAM_BOT_TOKEN  │               │
  │ Discord   │ ❌ no token │ —                 │ DISCORD_BOT_TOKEN   │ see note below│
  │ REST API  │ ✅ enabled  │ http://…:8000     │ RUN_API / API_HOST  │               │
  └─────────────────────────────────────────────────────────────────────────────┘
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
        "setup_hint": "curie channel bind-telegram <TOKEN>  or set TELEGRAM_BOT_TOKEN in .env",
    },
    {
        "name": "discord",
        "token_env": "DISCORD_BOT_TOKEN",
        "run_env": "RUN_DISCORD",
        "label": "Discord",
        "api_url": "https://discord.com/api/v10/gateway",
        "setup_hint": "curie channel bind-discord <TOKEN>  or set DISCORD_BOT_TOKEN in .env",
    },
    {
        "name": "api",
        "token_env": None,
        "run_env": "RUN_API",
        "label": "REST API",
        "api_url": None,
        "setup_hint": "Set RUN_API=true and optionally API_HOST/API_PORT in .env",
    },
]


# ─── Bot identity lookup ──────────────────────────────────────────────────────

def _telegram_identity(token: str) -> Optional[str]:
    """
    Query Telegram's getMe endpoint to retrieve the bot's username.
    Returns '@username' on success, or None on any error.
    """
    try:
        import urllib.request  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = _json.loads(r.read())
        if data.get("ok") and data.get("result", {}).get("username"):
            return "@" + data["result"]["username"]
    except Exception:
        pass
    return None


def _discord_identity(token: str) -> Optional[str]:
    """
    Query Discord's /users/@me endpoint to retrieve the bot's username.
    Returns 'BotName#0' on success, or None on any error.
    """
    try:
        import urllib.request  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read())
        name = data.get("username")
        discriminator = data.get("discriminator", "0")
        if name:
            return f"{name}#{discriminator}" if discriminator != "0" else name
    except Exception:
        pass
    return None


def _api_identity() -> str:
    """Return the base URL of the REST API connector."""
    host = os.getenv("API_HOST", "127.0.0.1")
    port = os.getenv("API_PORT", "8000")
    return f"http://{host}:{port}"


def _channel_status(ch: dict) -> dict:
    """Return a full status dict for a single channel config."""
    _load_dotenv()
    token = os.getenv(ch["token_env"]) if ch["token_env"] else None
    run_flag = os.getenv(ch["run_env"], "").lower()
    enabled = run_flag in ("1", "true", "yes") or (
        ch["name"] == "api" and run_flag not in ("0", "false", "no")
    )

    if ch["token_env"]:
        configured = bool(token)
        masked_token = (
            (token[:6] + "…" + token[-4:]) if token and len(token) > 10 else ("set" if token else "not set")
        )
    else:
        configured = True  # REST API has no token requirement
        masked_token = "N/A"

    return {
        "name": ch["name"],
        "label": ch["label"],
        "configured": configured,
        "enabled": enabled,
        "token": token,
        "token_display": masked_token,
        "token_env": ch["token_env"],
        "run_env": ch["run_env"],
        "setup_hint": ch.get("setup_hint", ""),
    }


def _lookup_identity(ch: dict, st: dict) -> str:
    """
    Attempt to resolve the bot's actual identity (username, URL) for *ch*.
    Returns a human-readable string, or '—' if not available.

    Note: this makes a live API call for Telegram and Discord.  Results are
    not cached; use ``curie channel doctor`` for repeated checks.
    """
    name = ch["name"]
    token = st.get("token")

    if name == "telegram" and token:
        identity = _telegram_identity(token)
        return identity if identity else "token set (getMe failed)"

    if name == "discord" and token:
        identity = _discord_identity(token)
        return identity if identity else "token set (API call failed)"

    if name == "api":
        return _api_identity()

    return "—"


# ─── Public commands ──────────────────────────────────────────────────────────


def cmd_channel_list(fetch_identity: bool = True) -> int:
    """
    List all channels with their configuration status.

    Columns:
      Channel    – platform name (Telegram, Discord, REST API)
      Status     – ✅ ready / ❌ no token / ⚠️  disabled
      Identity   – bot username (@CurieBot) or API base URL; live API call
      Token env  – environment variable name that holds the token
      Enabled    – whether the connector is activated (RUN_* flag)
    """
    _load_dotenv()

    rows = []
    for ch in _CHANNELS:
        st = _channel_status(ch)
        identity = _lookup_identity(ch, st) if fetch_identity else "—"

        if not st["configured"]:
            status_str = "❌ no token"
            status_rich = "[red]❌ no token[/red]"
        elif not st["enabled"]:
            status_str = "⚠️  disabled"
            status_rich = "[yellow]⚠️  disabled[/yellow]"
        else:
            status_str = "✅ ready"
            status_rich = "[green]✅ ready[/green]"

        rows.append({
            "label": st["label"],
            "status_str": status_str,
            "status_rich": status_rich,
            "identity": identity or "—",
            "token_env": st["token_env"] or "—",
            "enabled": st["enabled"],
            "configured": st["configured"],
            "setup_hint": st["setup_hint"],
        })

    if _RICH:
        table = Table(
            title="Curie AI – Channel Configuration",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Channel", style="bold white", min_width=10)
        table.add_column("Status", min_width=14)
        table.add_column("Identity", min_width=22)
        table.add_column("Token env", style="dim", min_width=24)

        for r in rows:
            table.add_row(r["label"], r["status_rich"], r["identity"], r["token_env"])

        _console.print(table)

        # Print setup hints for unconfigured channels
        needs_setup = [r for r in rows if not r["configured"]]
        if needs_setup:
            _console.print()
            _console.print("[bold yellow]Setup hints for unconfigured channels:[/bold yellow]")
            for r in needs_setup:
                _console.print(f"  [bold]{r['label']}:[/bold] {r['setup_hint']}")
    else:
        print(f"{'Channel':<12} {'Status':<16} {'Identity':<26} {'Token env'}")
        print("-" * 76)
        for r in rows:
            print(f"{r['label']:<12} {r['status_str']:<16} {r['identity']:<26} {r['token_env']}")

        needs_setup = [r for r in rows if not r["configured"]]
        if needs_setup:
            print()
            print("Setup hints:")
            for r in needs_setup:
                print(f"  {r['label']}: {r['setup_hint']}")

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
            _p(f"[yellow]⚠️  {label}:[/yellow] not configured  →  {st['setup_hint']}")
            continue

        api_url = ch.get("api_url")
        if not api_url:
            _p(f"[green]✅ {label}:[/green] local — no connectivity check needed  ({_api_identity()})")
            continue

        # Identity check (live API call)
        identity = _lookup_identity(ch, st)
        if identity and "failed" not in identity:
            _p(f"[green]✅ {label}:[/green] connected as [bold]{identity}[/bold]")
        else:
            # Fall back to plain connectivity check
            try:
                from urllib.parse import urlparse  # noqa: PLC0415
                import urllib.request  # noqa: PLC0415
                host = urlparse(api_url).hostname
                socket.setdefaulttimeout(4)
                socket.gethostbyname(host)
                _p(f"[green]✅ {label}:[/green] DNS reachable  ({identity})")
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
        # Verify identity immediately
        identity = _lookup_identity(ch, {"token": token})
        if identity and "failed" not in identity and identity != "—":
            _p(f"   Bot identity: [bold]{identity}[/bold]")
        _p(f"   Restart Curie to activate: [bold]curie restart[/bold]")
        return 0
    return 1


