# cli/auth.py
"""
LLM provider authentication management for Curie AI.

Stores API keys and provider preferences in the project .env file.

Commands:
  curie auth login --provider openai        – store API key for a provider
  curie auth status                         – show configured providers
  curie auth use --provider anthropic       – set provider priority in .env
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

try:
    from rich.prompt import Prompt as _Prompt
    def _prompt(label: str, password: bool = False) -> str:
        return _Prompt.ask(label, password=password)
except ImportError:
    def _prompt(label: str, password: bool = False) -> str:  # type: ignore[misc]
        import getpass
        return getpass.getpass(f"{label}: ") if password else input(f"{label}: ").strip()


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
    """Write or update a single key in the project .env file. Returns True on success."""
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


# ─── Provider definitions ─────────────────────────────────────────────────────

_PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI (GPT)",
        "key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o-mini",
        "key_hint": "sk-...",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "default_model": "claude-3-haiku-20240307",
        "key_hint": "sk-ant-...",
    },
    "gemini": {
        "label": "Google Gemini",
        "key_env": "GOOGLE_API_KEY",
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-1.5-flash",
        "key_hint": "AI...",
    },
    "llama.cpp": {
        "label": "llama.cpp (local)",
        "key_env": None,
        "model_env": "LLM_MODEL_PATH",
        "default_model": "",
        "key_hint": None,
    },
}

_PRIORITY_ENV = "LLM_PROVIDER_PRIORITY"


# ─── Public commands ──────────────────────────────────────────────────────────


def cmd_auth_login(provider: str, api_key: Optional[str] = None) -> int:
    """Store an API key for a provider."""
    _load_dotenv()

    provider = provider.lower()
    pdef = _PROVIDERS.get(provider)
    if pdef is None:
        _p(f"[red]❌ Unknown provider:[/red] {provider!r}")
        _p(f"   Supported: {', '.join(_PROVIDERS)}")
        return 1

    if pdef["key_env"] is None:
        _p(f"[yellow]{pdef['label']} is a local provider — no API key required.[/yellow]")
        _p(f"   Set [bold]{pdef['model_env']}[/bold] in your .env to point to the model file.")
        return 0

    if not api_key:
        api_key = _prompt(
            f"  Enter [cyan]{pdef['label']}[/cyan] API key (e.g. {pdef['key_hint']})",
            password=True,
        )

    if not api_key:
        _p("[yellow]No key provided. Aborting.[/yellow]")
        return 1

    if _update_env_key(pdef["key_env"], api_key):
        _p(f"[green]✅ {pdef['label']} API key saved.[/green]")
        # Suggest adding to priority if not already present
        current_priority = os.getenv(_PRIORITY_ENV, "llama.cpp")
        if provider not in current_priority.lower():
            _p(f"  Tip: run [bold]curie auth use --provider {provider}[/bold] to activate this provider.")
        return 0
    return 1


def cmd_auth_status() -> int:
    """Show which providers are configured."""
    _load_dotenv()

    current_priority = os.getenv(_PRIORITY_ENV, "llama.cpp")
    priority_list = [p.strip() for p in current_priority.split(",") if p.strip()]

    if _RICH:
        table = Table(
            title="Curie AI – LLM Provider Status",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Provider", style="bold white", min_width=22)
        table.add_column("Status", min_width=14)
        table.add_column("Active Priority", min_width=16)
        table.add_column("Model / Key env", style="dim", min_width=28)

        for name, pdef in _PROVIDERS.items():
            if pdef["key_env"]:
                key_val = os.getenv(pdef["key_env"])
                configured = bool(key_val)
                status = "[green]key set[/green]" if configured else "[yellow]no key[/yellow]"
            else:
                configured = True
                status = "[green]local[/green]"

            priority_idx = next((str(i + 1) for i, p in enumerate(priority_list) if p == name), "—")
            model_env = pdef["model_env"] or "—"
            table.add_row(pdef["label"], status, priority_idx, model_env)

        _console.print(table)
        _p(f"\nActive priority: [bold]{current_priority}[/bold]  (env: {_PRIORITY_ENV})")
    else:
        print(f"Active priority: {current_priority}")
        print()
        for name, pdef in _PROVIDERS.items():
            if pdef["key_env"]:
                configured = bool(os.getenv(pdef["key_env"]))
                status = "key set" if configured else "no key"
            else:
                status = "local"
            in_priority = name in priority_list
            print(f"  {'*' if in_priority else ' '} {pdef['label']:<28} {status}")

    return 0


def cmd_auth_use(provider: str, prepend: bool = True) -> int:
    """Set the active LLM provider (updates LLM_PROVIDER_PRIORITY in .env)."""
    _load_dotenv()

    provider = provider.lower()
    if provider not in _PROVIDERS:
        _p(f"[red]❌ Unknown provider:[/red] {provider!r}")
        _p(f"   Supported: {', '.join(_PROVIDERS)}")
        return 1

    current_priority = os.getenv(_PRIORITY_ENV, "llama.cpp")
    parts = [p.strip() for p in current_priority.split(",") if p.strip() and p.strip() != provider]

    if prepend:
        new_priority = ",".join([provider] + parts)
    else:
        new_priority = ",".join(parts + [provider])

    if _update_env_key(_PRIORITY_ENV, new_priority):
        _p(f"[green]✅ Provider priority updated:[/green] [bold]{new_priority}[/bold]")
        _p("  Restart Curie to apply: [bold]curie restart[/bold]")
        return 0
    return 1
