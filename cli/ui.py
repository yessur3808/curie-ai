# cli/ui.py
"""
Shared modern CLI utilities for Curie AI.

Public API
----------
  spinner(label, done_label)       Animated Rich spinner context manager
  step_progress(steps, title)      Sequential multi-step progress bar
  progress_bar(total, label)       Simple determinate progress-bar context manager
  live_tail(path, n_lines, label)  Rich-Live log viewer (Ctrl-C to exit)
  select(options, title, default)  Arrow-key / numbered single-choice selector
  multi_select(options, title)     Arrow-key / numbered multi-choice checkbox
  notify(title, body, urgency)     OS-aware desktop notification
  confirm(label, default)          Styled yes/no prompt
  print_rule(title)                Section divider
  success / info / warn / error    Styled one-line print helpers
  console                          The shared Rich Console (may be None)

All Rich elements degrade gracefully when ``rich`` is not installed.
``select`` / ``multi_select`` use raw-terminal arrow-key navigation on
Linux/macOS and a numbered fallback on Windows or non-TTY environments.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterable, List, Optional, Sequence

# ─── Rich availability ────────────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.prompt import Confirm as _RichConfirm
    from rich.rule import Rule
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _RICH = True
    console = Console()
    _console = console
except ImportError:
    _RICH = False
    console = None  # type: ignore[assignment]
    _console = None  # type: ignore[assignment]

# ─── OS detection ─────────────────────────────────────────────────────────────

_OS = platform.system()  # "Linux", "Darwin", "Windows"
_IS_TTY = sys.stdin.isatty() and sys.stdout.isatty()


# ═══════════════════════════════════════════════════════════════════════════════
# Simple print helpers
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# Spinner
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def spinner(label: str, done_label: Optional[str] = None) -> Generator[None, None, None]:
    """
    Animated spinner for the duration of the body block.

    Usage::

        with spinner("Scanning devices…", done_label="Scan complete"):
            results = expensive_scan()
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


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-step progress
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def step_progress(steps: List[str], title: str = "") -> Generator[None, None, None]:
    """
    Render a step-by-step progress bar that advances through *steps*.

    The generator yields *once per step*, advancing automatically.
    Callers just ``yield`` or ignore the value::

        with step_progress(["Load config", "Connect DB", "Render"]) as adv:
            load_config()   # first yield
            connect_db()    # second yield
            render()        # third yield
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
            yield prog
            prog.advance(task)


# ═══════════════════════════════════════════════════════════════════════════════
# Determinate progress bar
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def progress_bar(total: int, label: str = "Working…") -> Generator["_ProgressHandle", None, None]:
    """
    Simple determinate progress bar.

    Yields a handle with an ``advance(n=1)`` method::

        with progress_bar(len(files), "Processing files") as bar:
            for f in files:
                process(f)
                bar.advance()
    """
    class _ProgressHandle:
        def __init__(self, prog: "Progress", task: "TaskID") -> None:
            self._prog = prog
            self._task = task

        def advance(self, n: int = 1) -> None:
            if _RICH:
                self._prog.advance(self._task, n)

    if not _RICH:
        print(f"  {label} (0/{total})", flush=True)

        class _DummyHandle:
            _count = 0

            def advance(self, n: int = 1) -> None:
                self.__class__._count += n
                print(f"\r  {label} ({self.__class__._count}/{total})", end="", flush=True)

        yield _DummyHandle()  # type: ignore[misc]
        print()
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=_console,
    ) as prog:
        task = prog.add_task(label, total=total)
        yield _ProgressHandle(prog, task)


# ═══════════════════════════════════════════════════════════════════════════════
# Live log tail
# ═══════════════════════════════════════════════════════════════════════════════

# Log-level colour map used by live_tail
_LOG_COLOURS: dict[str, str] = {
    "error":    "bold red",
    "err":      "bold red",
    "critical": "bold magenta",
    "warning":  "yellow",
    "warn":     "yellow",
    "info":     "cyan",
    "debug":    "dim",
}


def _colourise_log_line(line: str) -> "Text":
    """Return a Rich Text object with log-level colour applied."""
    lower = line.lower()
    style = "white"
    for keyword, col in _LOG_COLOURS.items():
        if keyword in lower:
            style = col
            break
    return Text(line, style=style)


def live_tail(
    path: "Path | str",
    n_lines: int = 50,
    label: str = "",
) -> None:
    """
    Display the last *n_lines* of *path* and then follow it in real time
    inside a Rich Live panel.  Press Ctrl-C to exit.

    Falls back to plain ``print``/``readline`` when Rich is unavailable.
    """
    import time

    path = Path(path)

    if not path.exists():
        error(f"Log file not found: {path}")
        return

    # Read initial tail
    try:
        raw_lines = path.read_text(errors="replace").splitlines()
        tail = raw_lines[-n_lines:]
    except OSError as exc:
        error(f"Cannot read log: {exc}")
        return

    title = label or f"📄 {path.name}"

    if not _RICH:
        for line in tail:
            print(line)
        try:
            with open(path, errors="replace") as fh:
                fh.seek(0, 2)
                while True:
                    line = fh.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        return

    def _build_panel(lines: list[str]) -> "Panel":
        from rich.text import Text as _Text
        content = _Text()
        for ln in lines:
            content.append_text(_colourise_log_line(ln))
            content.append("\n")
        return Panel(
            content,
            title=f"[bold cyan]{title}[/bold cyan]",
            subtitle="[dim]Ctrl-C to exit[/dim]",
            box=box.ROUNDED,
        )

    with Live(_build_panel(tail), console=_console, refresh_per_second=4, screen=False) as live:
        try:
            with open(path, errors="replace") as fh:
                fh.seek(0, 2)
                while True:
                    line = fh.readline()
                    if line:
                        tail.append(line.rstrip())
                        tail = tail[-n_lines:]
                        live.update(_build_panel(tail))
                    else:
                        time.sleep(0.2)
        except KeyboardInterrupt:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Arrow-key interactive selector
# ═══════════════════════════════════════════════════════════════════════════════

def _raw_read_key() -> str:
    """
    Read a single keypress from stdin in raw mode.

    Returns one of: 'up', 'down', 'enter', 'space', a printable character,
    or '' on error.  Raises KeyboardInterrupt on Ctrl-C.

    Only available on POSIX systems with a real TTY.
    """
    try:
        import tty
        import termios
    except ImportError:
        return ""

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1).decode("utf-8", errors="replace")
        if ch == "\x1b":                            # ESC sequence
            ch2 = os.read(fd, 1).decode("utf-8", errors="replace")
            if ch2 == "[":
                ch3 = os.read(fd, 1).decode("utf-8", errors="replace")
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(ch3, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":                            # Ctrl-D
            raise EOFError
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _can_use_arrow_keys() -> bool:
    """True when we can safely use raw-mode arrow-key navigation."""
    if _OS == "Windows":
        return False
    return _IS_TTY


def select(
    options: Sequence[str],
    title: str = "Choose one",
    default: int = 0,
) -> int:
    """
    Present an interactive single-choice selector.

    On TTY / POSIX: renders a live arrow-key menu via Rich.
    Fallback (Windows / non-TTY / no Rich): numbered list with text input.

    Returns the index of the chosen option (0-based).
    Raises ``KeyboardInterrupt`` if the user presses Ctrl-C.

    Example::

        idx = select(["openai", "anthropic", "gemini", "llama.cpp"],
                     title="Choose LLM provider")
        provider = ["openai", "anthropic", "gemini", "llama.cpp"][idx]
    """
    if not options:
        raise ValueError("select() requires at least one option")

    if not _can_use_arrow_keys() or not _RICH:
        return _select_numbered(options, title, default)
    return _select_arrow(options, title, default)


def _select_numbered(
    options: Sequence[str],
    title: str,
    default: int,
) -> int:
    """Numbered fallback selector – works without TTY / Rich."""
    print(f"\n  {title}")
    for i, opt in enumerate(options):
        marker = "▶" if i == default else " "
        print(f"  {marker} {i + 1}. {opt}")
    while True:
        raw = input(f"\n  Enter number [1-{len(options)}] (default {default + 1}): ").strip()
        if not raw:
            return default
        try:
            n = int(raw) - 1
            if 0 <= n < len(options):
                return n
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(options)}.")


def _select_arrow(
    options: Sequence[str],
    title: str,
    default: int,
) -> int:
    """Arrow-key interactive selector using Rich Live."""
    current = max(0, min(default, len(options) - 1))

    def _render() -> "Panel":
        rows: list[str] = []
        for i, opt in enumerate(options):
            if i == current:
                rows.append(f"[bold cyan] ▶  {opt}[/bold cyan]")
            else:
                rows.append(f"[dim]   {opt}[/dim]")
        body = "\n".join(rows)
        return Panel(
            body,
            title=f"[bold]{title}[/bold]",
            subtitle="[dim]↑/↓ navigate   Enter select   Ctrl-C cancel[/dim]",
            box=box.ROUNDED,
            padding=(1, 3),
        )

    with Live(_render(), console=_console, refresh_per_second=20, screen=False) as live:
        while True:
            key = _raw_read_key()
            if key == "up":
                current = (current - 1) % len(options)
            elif key == "down":
                current = (current + 1) % len(options)
            elif key == "enter":
                break
            live.update(_render())

    if _RICH:
        _console.print(f"  [bold cyan]▶[/bold cyan]  [white]{options[current]}[/white]")
    return current


def multi_select(
    options: Sequence[str],
    title: str = "Choose (Space to toggle, Enter to confirm)",
    defaults: Optional[Sequence[int]] = None,
) -> list[int]:
    """
    Present an interactive multi-choice checkbox selector.

    On TTY / POSIX: renders a live arrow-key + Space-toggle menu via Rich.
    Fallback: comma-separated numbered input.

    Returns a list of selected indices (0-based).  May be empty.
    Raises ``KeyboardInterrupt`` if the user presses Ctrl-C.

    Example::

        connectors = ["--api", "--telegram", "--discord"]
        chosen = multi_select(connectors, "Enable connectors")
        # chosen = [0, 1]  → ["--api", "--telegram"]
    """
    if not options:
        return []

    selected: set[int] = set(defaults or [])

    if not _can_use_arrow_keys() or not _RICH:
        return _multi_select_numbered(options, title, selected)
    return _multi_select_arrow(options, title, selected)


def _multi_select_numbered(
    options: Sequence[str],
    title: str,
    selected: set[int],
) -> list[int]:
    """Numbered fallback multi-selector."""
    print(f"\n  {title}")
    for i, opt in enumerate(options):
        check = "✓" if i in selected else " "
        print(f"  [{check}] {i + 1}. {opt}")
    raw = input(
        f"\n  Enter numbers to select (comma-separated, e.g. 1,3) or leave blank for none: "
    ).strip()
    if not raw:
        return sorted(selected)
    result: list[int] = []
    for part in raw.split(","):
        try:
            n = int(part.strip()) - 1
            if 0 <= n < len(options):
                result.append(n)
        except ValueError:
            pass
    return sorted(set(result))


def _multi_select_arrow(
    options: Sequence[str],
    title: str,
    selected: set[int],
) -> list[int]:
    """Arrow-key + Space-toggle multi-selector using Rich Live."""
    current = 0
    selected = set(selected)

    def _render() -> "Panel":
        rows: list[str] = []
        for i, opt in enumerate(options):
            check = "[bold green]✓[/bold green]" if i in selected else "[dim]○[/dim]"
            if i == current:
                rows.append(f"[bold cyan] ▶ {check}  {opt}[/bold cyan]")
            else:
                rows.append(f"[dim]   {check}[/dim]  {opt}")
        body = "\n".join(rows)
        n_sel = len(selected)
        return Panel(
            body,
            title=f"[bold]{title}[/bold]",
            subtitle=(
                f"[dim]↑/↓ navigate   Space toggle   Enter confirm "
                f"({n_sel} selected)   Ctrl-C cancel[/dim]"
            ),
            box=box.ROUNDED,
            padding=(1, 3),
        )

    with Live(_render(), console=_console, refresh_per_second=20, screen=False) as live:
        while True:
            key = _raw_read_key()
            if key == "up":
                current = (current - 1) % len(options)
            elif key == "down":
                current = (current + 1) % len(options)
            elif key == "space":
                if current in selected:
                    selected.discard(current)
                else:
                    selected.add(current)
            elif key == "enter":
                break
            live.update(_render())

    # Print summary
    if _RICH and selected:
        chosen_names = ", ".join(options[i] for i in sorted(selected))
        _console.print(f"  [bold cyan]Selected:[/bold cyan] [white]{chosen_names}[/white]")
    return sorted(selected)


# ═══════════════════════════════════════════════════════════════════════════════
# Desktop notifications
# ═══════════════════════════════════════════════════════════════════════════════

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
            urgency_flag = {"low": "low", "normal": "normal", "critical": "critical"}.get(
                urgency, "normal"
            )
            subprocess.Popen(
                ["notify-send", "-u", urgency_flag, "-a", "Curie AI", title, body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True

        if _OS == "Darwin" and shutil.which("osascript"):
            t = title.replace('"', '\\"')
            b = body.replace('"', '\\"')
            subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    f'display notification "{b}" with title "{t}" subtitle "Curie AI"',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True

        if _OS == "Windows":
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if ps:
                t = title.replace("'", "''")
                b = body.replace("'", "''")
                script = (
                    f"Import-Module BurntToast -ErrorAction SilentlyContinue; "
                    f"New-BurntToastNotification -Text '{t}','{b}'"
                )
                subprocess.Popen(
                    [ps, "-Command", script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Styled confirm prompt
# ═══════════════════════════════════════════════════════════════════════════════

def confirm(label: str, default: bool = True) -> bool:
    """Prompt the user for a yes/no answer with consistent Rich styling."""
    if _RICH:
        return _RichConfirm.ask(f"[cyan]{label}[/cyan]", default=default)
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {label} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")
