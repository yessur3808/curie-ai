# cli/tasks_display.py
"""
Rich display helpers for task/sub-agent breakdown.
Separate from cli/tasks.py so that the registry can be imported without rich.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


def _age(ts: float | None) -> str:
    if ts is None:
        return "—"
    secs = int(time.time() - ts)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _status_style(status: str) -> str:
    return {
        "running": "green",
        "done": "cyan",
        "failed": "red",
        "cancelled": "yellow",
    }.get(status, "white")


def _build_task_table(tasks: list[dict[str, Any]], show_finished: bool = False) -> "Table":
    table = Table(
        title="🤖 Curie AI – Task & Sub-Agent Breakdown",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("#", width=4)
    table.add_column("Task ID", min_width=12)
    table.add_column("Description", min_width=30)
    table.add_column("Channel", min_width=10)
    table.add_column("Status", min_width=8)
    table.add_column("Age", min_width=8)
    table.add_column("Sub-agents", min_width=10)

    visible = [t for t in tasks if show_finished or t.get("status") == "running"]

    if not visible:
        table.add_row("—", "—", "[dim]No active tasks[/dim]", "—", "—", "—", "—")
        return table

    for i, task in enumerate(visible, 1):
        sub_agents = task.get("sub_agents", {})
        running_agents = sum(1 for a in sub_agents.values() if a.get("status") == "running")
        total_agents = len(sub_agents)
        status = task.get("status", "?")
        table.add_row(
            str(i),
            task.get("id", "?")[:12],
            task.get("description", "?")[:50],
            task.get("channel", "?"),
            Text(status, style=_status_style(status)),
            _age(task.get("started_at")),
            f"{running_agents}/{total_agents} running",
        )

    return table


def _build_sub_agent_table(task: dict[str, Any]) -> "Table":
    table = Table(
        title=f"Sub-agents for task: {task.get('id', '?')} – {task.get('description', '')[:40]}",
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Agent ID", min_width=14)
    table.add_column("Role", min_width=20)
    table.add_column("Model", min_width=14)
    table.add_column("Status", min_width=8)
    table.add_column("Duration", min_width=8)
    table.add_column("Summary", min_width=30)

    sub_agents = task.get("sub_agents", {})
    if not sub_agents:
        table.add_row("—", "[dim]No sub-agents registered[/dim]", "—", "—", "—", "—")
        return table

    for agent in sub_agents.values():
        started = agent.get("started_at")
        finished = agent.get("finished_at")
        if finished and started:
            elapsed = int(finished - started)
            dur_str = f"{elapsed}s"
        else:
            dur_str = _age(started) if started else "—"
        status = agent.get("status", "?")
        table.add_row(
            agent.get("id", "?")[:14],
            agent.get("role", "?")[:25],
            agent.get("model", "?")[:14] or "—",
            Text(status, style=_status_style(status)),
            dur_str,
            (agent.get("result_summary") or "")[:40],
        )
    return table


def show_tasks(show_finished: bool = False, live: bool = False) -> None:
    """Display task table. If live=True, refresh every second until Ctrl-C."""
    if not RICH_AVAILABLE:
        print("rich is required: pip install rich")
        return

    from cli.tasks import get_tasks, get_task_summary

    if live:
        with Live(console=console, refresh_per_second=1, screen=False) as lv:
            try:
                while True:
                    tasks = get_tasks()
                    summary = get_task_summary()
                    header = (
                        f"  [bold]Tasks:[/bold] {summary['running_tasks']} running / "
                        f"{summary['total_tasks']} total   "
                        f"[bold]Sub-agents:[/bold] {summary['running_sub_agents']} running / "
                        f"{summary['total_sub_agents']} total"
                    )
                    from rich.console import Group
                    lv.update(Group(
                        Panel(header, box=box.MINIMAL),
                        _build_task_table(tasks, show_finished),
                    ))
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    else:
        tasks = get_tasks()
        summary = get_task_summary()
        console.print(
            f"  Tasks: [bold]{summary['running_tasks']}[/bold] running / "
            f"{summary['total_tasks']} total   "
            f"Sub-agents: [bold]{summary['running_sub_agents']}[/bold] running / "
            f"{summary['total_sub_agents']} total"
        )
        console.print(_build_task_table(tasks, show_finished))

        # Show per-task sub-agent breakdown for running tasks
        running = [t for t in tasks if t.get("status") == "running"]
        for task in running:
            if task.get("sub_agents"):
                console.print(_build_sub_agent_table(task))
