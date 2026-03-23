# cli/ui.py
"""
Shared modern CLI utilities for Curie AI.

Provides:
  spinner(label)           – animated Rich spinner context manager
  step_progress(steps)     – multi-step progress bar context manager
  notify(title, body)      – OS-aware desktop notification
  confirm(label, default)  – styled yes/no prompt
  print_rule(title)        – section divider
  success / info / warn / error  – styled one-line messages

All Rich elements degrade gracefully when rich is not installed.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from contextlib import contextmanager
from typing import Generator, List, Optional

# ─── Rich availability ────────────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.progress import (
        Progress, SpinnerColumn, TextColumn, BarColumn,
        TaskProgressColumn, TimeElapsedColumn, MofNCompleteColumn,
    )
    from rich.prompt import Confirm as _RichConfirm
    from rich.rule import Rule
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None  # type: ignore[assignment]

# ─── Detect OS ────────────────────────────────────────────────────────────────

_OS = platform.system()  # "Linux", "Darwin", "Windows"


# ─── Simple print helpers ─────────────────────────────────────────────────────

def success(msg: str) -> None:
    if _RICH:
        _console.print(f"[bold green]✅ {msg}[/bold green]")
    else:
        print(f"✅  {msg}")


def info(msg: str) -> None:
    if _RICH:
        _console.print(f"[cyan]ℹ  {msg}[/cyan]")
    else:
        print(f"   {msg}")


def warn(msg: str) -> None:
    if _RICH:
        _console.print(f"[yellow]⚠️  {msg}[/yellow]")
    else:
        print(f"⚠️  {msg}")


def error(msg: str) -> None:
    if _RICH:
        _console.print(f"[bold red]❌ {msg}[/bold red]")
    else:
        print(f"❌ {msg}", file=sys.stderr)


def print_rule(title: str = "") -> None:
    if _RICH:
        _console.print(Rule(title, style="dim cyan"))
    else:
        width = shutil.get_terminal_size().columns
        if title:
            pad = max(0, (width - len(title) - 2) // 2)
            print("─" * pad + f" {title} " + "─" * pad)
        else:
            print("─" * width)


# ─── Spinner ─────────────────────────────────────────────────────────────────

@contextmanager
def spinner(label: str, done_label: Optional[str] = None) -> Generator[None, None, None]:
    """
    Context manager that shows an animated spinner while the body executes.

    Usage::

        with spinner("Scanning devices…"):
            results = expensive_scan()

    When ``rich`` is unavailable falls back to a plain print.
    """
    if not _RICH:
        print(f"  {label}", flush=True)
        yield
        if done_label:
            print(f"  {done_label}")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=_console,
        transient=True,
    ) as prog:
        prog.add_task(label, total=None)
        yield

    if done_label:
        success(done_label)


# ─── Multi-step progress ──────────────────────────────────────────────────────

@contextmanager
def step_progress(steps: List[str], title: str = "") -> Generator[None, None, None]:
    """
    Context manager that renders a step-by-step progress bar.

    The body receives no special object; caller advances steps by calling
    ``advance()`` on the yielded Progress object.

    Usage::

        steps = ["Connect to DB", "Fetch users", "Render table"]
        with step_progress(steps, "Loading memory") as prog:
            with get_pg_conn() as conn: ...
            prog.advance(task)
            ...
    """
    if not _RICH:
        if title:
            print(f"\n{title}")
        for i, s in enumerate(steps, 1):
            print(f"  [{i}/{len(steps)}] {s}")
        yield None
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=_console,
    ) as prog:
        task = prog.add_task(title or "Progress", total=len(steps))
        for step in steps:
            prog.update(task, description=step)
            yield prog  # caller can call prog.advance(task)
            prog.advance(task)


# ─── Desktop notifications ────────────────────────────────────────────────────

def notify(title: str, body: str, urgency: str = "normal") -> bool:
    """
    Send a desktop notification using the OS-native mechanism.

    - Linux:   ``notify-send``
    - macOS:   ``osascript``
    - Windows: ``powershell`` toast via BurntToast module (if available)

    Returns True if the notification was dispatched successfully.
    Silent on failure (notifications are always best-effort).
    """
    try:
        if _OS == "Linux" and shutil.which("notify-send"):
            urgency_flag = {"low": "low", "normal": "normal", "critical": "critical"}.get(urgency, "normal")
            subprocess.Popen(
                ["notify-send", "-u", urgency_flag, "-a", "Curie AI", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True

        if _OS == "Darwin" and shutil.which("osascript"):
            # Escape single quotes in title/body
            t = title.replace("'", "\\'")
            b = body.replace("'", "\\'")
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "{b}" with title "{t}" subtitle "Curie AI"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True

        if _OS == "Windows":
            # Try BurntToast PowerShell module
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if ps:
                script = (
                    f"Import-Module BurntToast -ErrorAction SilentlyContinue; "
                    f"New-BurntToastNotification -Text '{title}','{body}'"
                )
                subprocess.Popen(
                    [ps, "-Command", script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
    except Exception:
        pass
    return False


# ─── Styled confirm prompt ────────────────────────────────────────────────────

def confirm(label: str, default: bool = True) -> bool:
    """Prompt the user for a yes/no answer with consistent Rich styling."""
    if _RICH:
        return _RichConfirm.ask(f"[cyan]{label}[/cyan]", default=default)
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {label} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")
