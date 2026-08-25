"""Combined live log viewer for one or all Curie instances."""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from cli import daemon
from cli.dashboard import discover_instances

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover
    Console = None  # type: ignore[assignment,misc]


_INSTANCE_STYLES = ("cyan", "magenta", "green", "yellow", "blue", "bright_cyan")


def _log_paths(instance: str) -> dict[str, Path]:
    names = discover_instances() if instance == "all" else [daemon._instance_name(instance)]
    return {name: daemon._instance_paths(name)[1] for name in names}


def _matches_level(line: str, level: str) -> bool:
    if level == "all":
        return True
    lowered = line.casefold()
    aliases = {
        "error": ("error", "exception", "critical", "traceback"),
        "warning": ("warning", "warn"),
        "info": ("info",),
        "debug": ("debug",),
    }
    return any(token in lowered for token in aliases[level])


def _severity(line: str) -> tuple[str, str]:
    lowered = line.casefold()
    if any(word in lowered for word in ("error", "exception", "critical", "traceback")):
        return "ERROR", "bold red"
    if "warning" in lowered or "warn" in lowered:
        return "WARN", "yellow"
    if "debug" in lowered:
        return "DEBUG", "dim"
    return "INFO", "white"


class LogFollower:
    """Incrementally read multiple files without invoking external ``tail``."""

    def __init__(self, paths: dict[str, Path], lines: int = 100, level: str = "all"):
        self.paths = paths
        self.level = level
        self.entries: deque[tuple[str, str]] = deque(maxlen=max(20, lines))
        self.offsets: dict[str, int] = {}
        self._load_initial(lines)

    def _load_initial(self, count: int) -> None:
        initial: list[tuple[float, str, str]] = []
        for name, path in self.paths.items():
            try:
                content = path.read_text(errors="replace")
                self.offsets[name] = path.stat().st_size
                mtime = path.stat().st_mtime
                initial.extend((mtime, name, line) for line in content.splitlines()[-count:])
            except OSError:
                self.offsets[name] = 0
        for _, name, line in sorted(initial, key=lambda item: item[0]):
            if _matches_level(line, self.level):
                self.entries.append((name, line))

    def poll(self) -> int:
        added = 0
        for name, path in self.paths.items():
            try:
                size = path.stat().st_size
                offset = self.offsets.get(name, 0)
                if size < offset:  # log rotation or truncation
                    offset = 0
                if size == offset:
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    self.offsets[name] = handle.tell()
                for line in chunk.splitlines():
                    if _matches_level(line, self.level):
                        self.entries.append((name, line))
                        added += 1
            except OSError:
                continue
        return added


def _render(follower: LogFollower, instance: str) -> "Panel":
    table = Table(box=None, expand=True, show_header=True, header_style="bold cyan")
    table.add_column("Instance", width=12, no_wrap=True)
    table.add_column("Level", width=7, no_wrap=True)
    table.add_column("Message", overflow="fold")
    styles = {name: _INSTANCE_STYLES[index % len(_INSTANCE_STYLES)] for index, name in enumerate(follower.paths)}
    for name, line in follower.entries:
        level, style = _severity(line)
        table.add_row(Text(name, style=styles[name]), Text(level, style=style), Text(line, style=style))
    if not follower.entries:
        table.add_row("—", "—", Text("Waiting for matching log entries…", style="dim"))
    scope = "all instances" if instance == "all" else instance
    return Panel(table, title=f"Curie live logs · {scope} · Ctrl-C to exit", border_style="cyan")


def show_live_logs(
    *, instance: str = "all", lines: int = 100, level: str = "all",
    refresh_rate: float = 0.5, once: bool = False,
) -> None:
    follower = LogFollower(_log_paths(instance), lines=max(1, lines), level=level)
    if Console is None:
        for name, line in follower.entries:
            print(f"[{name}] {line}")
        if once:
            return
        try:
            while True:
                time.sleep(max(0.1, refresh_rate))
                previous = len(follower.entries)
                follower.poll()
                for name, line in list(follower.entries)[previous:]:
                    print(f"[{name}] {line}", flush=True)
        except KeyboardInterrupt:
            return

    console = Console()
    if once:
        console.print(_render(follower, instance))
        return
    refresh_rate = max(0.1, refresh_rate)
    with Live(_render(follower, instance), console=console, screen=False,
              refresh_per_second=max(1, 1 / refresh_rate)) as live:
        try:
            while True:
                time.sleep(refresh_rate)
                follower.poll()
                live.update(_render(follower, instance))
        except KeyboardInterrupt:
            pass
