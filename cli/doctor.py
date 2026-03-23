# cli/doctor.py
"""
System diagnostics for Curie AI.
Checks Python version, dependencies, environment variables, connectivity, etc.
"""

from __future__ import annotations

import sys
import os
import importlib
import subprocess
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

_console = Console() if RICH_AVAILABLE else None

# ─── helpers ─────────────────────────────────────────────────────────────────

_OK = "✅"
_WARN = "⚠️ "
_FAIL = "❌"


def _check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> tuple[str, str, str]:
    icon = _OK if ok else (_WARN if warn_only else _FAIL)
    return (icon, label, detail)


# ─── individual checks ────────────────────────────────────────────────────────


def _check_python() -> list[tuple[str, str, str]]:
    v = sys.version_info
    ok = v >= (3, 10)
    return [_check(f"Python {v.major}.{v.minor}.{v.micro}", ok,
                   "Requires >= 3.10" if not ok else "")]


def _check_core_deps() -> list[tuple[str, str, str]]:
    core = [
        "fastapi", "uvicorn", "psycopg2", "pymongo",
        "dotenv", "requests", "httpx", "rich",
    ]
    rows = []
    for mod in core:
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", "?")
            rows.append(_check(mod, True, ver))
        except ImportError:
            rows.append(_check(mod, False, "not installed"))
    return rows


def _check_optional_deps() -> list[tuple[str, str, str]]:
    optional = [
        ("psutil", "System metrics (curie metrics)"),
        ("telegram", "Telegram connector"),
        ("discord", "Discord connector"),
        ("llama_cpp", "Local LLM (llama-cpp-python)"),
        ("pynvml", "NVIDIA GPU monitoring"),
    ]
    rows = []
    for mod, desc in optional:
        try:
            importlib.import_module(mod)
            rows.append((_OK, f"{mod} ({desc})", "installed"))
        except ImportError:
            rows.append((_WARN, f"{mod} ({desc})", "not installed (optional)"))
    return rows


def _check_env_vars() -> list[tuple[str, str, str]]:
    # Load .env if present
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass

    required = [
        "POSTGRES_DSN",
        "MONGODB_URI",
    ]
    optional_env = [
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "MASTER_USER_ID",
        "LLM_PROVIDER_PRIORITY",
        "RUN_API",
        "RUN_TELEGRAM",
    ]
    rows = []
    for k in required:
        val = os.getenv(k)
        rows.append(_check(k, bool(val), "set" if val else "not set (required)"))
    for k in optional_env:
        val = os.getenv(k)
        rows.append(_check(k, True, "set" if val else "not set", warn_only=True))
    return rows


def _check_services() -> list[tuple[str, str, str]]:
    rows = []
    # Check if main.py exists
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    rows.append(_check("main.py", main_py.exists(), str(main_py)))

    # Check assets/personality dir
    assets = Path(__file__).resolve().parent.parent / "assets" / "personality"
    rows.append(_check("Personality assets", assets.exists(), str(assets)))

    # Check pid file / daemon
    from cli.daemon import get_status
    st = get_status()
    rows.append(_check(
        "Curie daemon",
        st["running"],
        f"PID {st['pid']} running" if st["running"] else "not running",
        warn_only=True,
    ))
    return rows


def _check_network() -> list[tuple[str, str, str]]:
    rows = []
    try:
        import socket
        socket.setdefaulttimeout(3)
        socket.gethostbyname("api.telegram.org")
        rows.append(_check("DNS resolution (api.telegram.org)", True))
    except Exception as e:
        rows.append(_check("DNS resolution", False, str(e), warn_only=True))

    try:
        import requests
        r = requests.get("https://api.telegram.org", timeout=4)
        rows.append(_check("HTTP connectivity (Telegram)", r.status_code < 500, f"HTTP {r.status_code}"))
    except Exception as e:
        rows.append(_check("HTTP connectivity (Telegram)", False, str(e), warn_only=True))

    return rows


# ─── main public function ─────────────────────────────────────────────────────


def run_doctor(verbose: bool = False) -> int:
    """
    Run all checks and print a report.

    Returns 0 if everything is OK, 1 if there are failures.
    """
    all_checks: list[tuple[str, list[tuple[str, str, str]]]] = [
        ("Python Runtime", _check_python()),
        ("Core Dependencies", _check_core_deps()),
        ("Optional Dependencies", _check_optional_deps()),
        ("Environment Variables", _check_env_vars()),
        ("Files & Daemon", _check_services()),
        ("Network", _check_network()),
    ]

    has_failure = False

    if RICH_AVAILABLE:
        for section, rows in all_checks:
            table = Table(
                title=section,
                box=box.SIMPLE_HEAD,
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Status", width=4)
            table.add_column("Check", min_width=30)
            table.add_column("Detail", min_width=30)
            for icon, label, detail in rows:
                if icon == _FAIL:
                    has_failure = True
                table.add_row(icon, label, detail)
            _console.print(table)
    else:
        for section, rows in all_checks:
            print(f"\n=== {section} ===")
            for icon, label, detail in rows:
                if icon == _FAIL:
                    has_failure = True
                print(f"  {icon} {label:<40} {detail}")

    if has_failure:
        if RICH_AVAILABLE:
            _console.print("[red]Some checks failed. Review the issues above.[/red]")
        else:
            print("\n❌ Some checks failed.")
        return 1
    if RICH_AVAILABLE:
        _console.print("[green]All checks passed! ✅[/green]")
    else:
        print("\n✅ All checks passed!")
    return 0
