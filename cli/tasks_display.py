# cli/tasks_display.py
"""
Rich display helpers for task/sub-agent breakdown.
Separate from cli/tasks.py so that the registry can be imported without rich.

Provides two views:
  show_tasks()      – classic table view (--live for auto-refresh)
  show_agent_tree() – beautiful tree view  (--live for auto-refresh, --tree flag)
"""

from __future__ import annotations

import time
from typing import Any

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.live import Live
    from rich.text import Text
    from rich.tree import Tree
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# Rotating "thinking" frames used for running-agent animation.
_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _age(ts: float | None) -> str:
    if ts is None:
        return "—"
    secs = int(time.time() - ts)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def _dur(started: float | None, finished: float | None) -> str:
    """Return a human-readable elapsed/duration string."""
    if started is None:
        return "—"
    end = finished if finished else time.time()
    secs = int(end - started)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _status_style(status: str) -> str:
    return {
        "running": "green",
        "done": "cyan",
        "failed": "red",
        "cancelled": "yellow",
    }.get(status, "white")


def _status_icon(status: str) -> str:
    return {
        "running": "⚡",
        "done": "✅",
        "failed": "❌",
        "cancelled": "⚠️",
    }.get(status, "❓")


# ---------------------------------------------------------------------------
# Table view (original, kept for default)
# ---------------------------------------------------------------------------


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
    table.add_column("Activity / Summary", min_width=36)

    sub_agents = task.get("sub_agents", {})
    if not sub_agents:
        table.add_row("—", "[dim]No sub-agents registered[/dim]", "—", "—", "—", "—")
        return table

    for agent in sub_agents.values():
        started = agent.get("started_at")
        finished = agent.get("finished_at")
        dur_str = _dur(started, finished)
        status = agent.get("status", "?")
        # Prefer live description; fall back to result summary
        activity = agent.get("description") or agent.get("result_summary") or ""
        table.add_row(
            agent.get("id", "?")[:14],
            agent.get("role", "?")[:25],
            agent.get("model", "?")[:14] or "—",
            Text(status, style=_status_style(status)),
            dur_str,
            activity[:50],
        )
    return table


# ---------------------------------------------------------------------------
# Tree view (new visualization)
# ---------------------------------------------------------------------------

# Role → friendly label mapping
_ROLE_LABELS: dict[str, str] = {
    "llm_inference":    "🧠 LLM Inference",
    "coding_assistant": "💻 Coding Assistant",
    "navigation":       "🗺️  Navigation",
    "scheduler":        "⏰ Scheduler",
    "trip_planner":     "✈️  Trip Planner",
    "system_commands":  "⚙️  System Commands",
}


def _friendly_role(role: str) -> str:
    return _ROLE_LABELS.get(role, f"🔧 {role}")


def _build_agent_tree(
    tasks: list[dict[str, Any]],
    show_finished: bool = False,
    tick: int = 0,
) -> "Tree":
    """
    Build a Rich Tree that shows the full task → sub-agent hierarchy.

    Each task is a branch; each sub-agent is a leaf with its role,
    current activity / result, and elapsed time.  Running agents display
    an animated spinner frame so the tree feels live even in static renders.
    """
    root = Tree("🤖 [bold cyan]Curie AI – Agent Activity[/bold cyan]")

    visible = [t for t in tasks if show_finished or t.get("status") == "running"]
    if not visible:
        root.add("[dim italic]No active tasks – waiting for messages…[/dim italic]")
        return root

    for task in visible:
        t_status = task.get("status", "?")
        t_icon = _status_icon(t_status)
        t_age = _age(task.get("started_at"))
        t_channel = task.get("channel", "?")
        t_desc = (task.get("description") or "(no description)")[:60]
        t_id = task.get("id", "?")[:8]

        style = _status_style(t_status)
        task_label = (
            f"{t_icon} [{style} bold]{t_desc}[/{style} bold]  "
            f"[dim]#{t_id} · {t_channel} · {t_age}[/dim]"
        )
        task_branch = root.add(task_label)

        sub_agents = task.get("sub_agents", {})
        if not sub_agents:
            task_branch.add("[dim italic]No sub-agents registered[/dim italic]")
            continue

        for agent in sub_agents.values():
            ag_status = agent.get("status", "?")
            ag_role = _friendly_role(agent.get("role", "?"))
            ag_started = agent.get("started_at")
            ag_finished = agent.get("finished_at")
            ag_dur = _dur(ag_started, ag_finished)

            # Live description (what the agent is doing right now)
            activity = agent.get("description") or agent.get("result_summary") or ""

            if ag_status == "running":
                spin = _SPIN_FRAMES[tick % len(_SPIN_FRAMES)]
                ag_label = (
                    f"[green]{spin}[/green] [green bold]{ag_role}[/green bold]  "
                    f"[green dim]{activity}[/green dim]  "
                    f"[dim]({ag_dur})[/dim]"
                )
            elif ag_status == "done":
                summary = activity or "done"
                if summary == "skipped":
                    ag_label = f"[dim]· {ag_role}  [italic]{summary}[/italic][/dim]"
                else:
                    ag_label = (
                        f"[cyan]✔[/cyan] [cyan bold]{ag_role}[/cyan bold]  "
                        f"[cyan dim]{summary}[/cyan dim]  "
                        f"[dim]({ag_dur})[/dim]"
                    )
            elif ag_status == "failed":
                ag_label = (
                    f"[red]✖[/red] [red bold]{ag_role}[/red bold]  "
                    f"[red dim]{activity}[/red dim]  "
                    f"[dim]({ag_dur})[/dim]"
                )
            else:
                ag_label = f"[dim]{ag_role}  {ag_status}[/dim]"

            task_branch.add(ag_label)

    return root


def _summary_panel(summary: dict[str, int]) -> "Panel":
    """Render a compact header panel with aggregate counts."""
    rt = summary.get("running_tasks", 0)
    tt = summary.get("total_tasks", 0)
    ra = summary.get("running_sub_agents", 0)
    ta = summary.get("total_sub_agents", 0)
    content = (
        f"  [bold]Tasks:[/bold] [green]{rt}[/green] running / {tt} total"
        f"    [bold]Sub-agents:[/bold] [green]{ra}[/green] running / {ta} total"
    )
    return Panel(content, box=box.MINIMAL, padding=(0, 1))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def show_tasks(show_finished: bool = False, live: bool = False, tree: bool = False) -> None:
    """
    Display task/sub-agent breakdown.

    Parameters
    ----------
    show_finished : include finished/failed tasks (default: running only)
    live          : refresh every second until Ctrl-C
    tree          : use Rich Tree view instead of the default table view
    """
    if not RICH_AVAILABLE:
        print("rich is required: pip install rich")
        return

    from cli.tasks import get_tasks, get_task_summary

    if tree:
        show_agent_tree(show_finished=show_finished, live=live)
        return

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


def show_agent_tree(show_finished: bool = False, live: bool = False) -> None:
    """
    Display a beautiful Rich Tree visualization of tasks and their sub-agents.

    Each task is shown as a branch with its sub-agents as leaves,
    including what each agent is currently doing.  When ``live=True``,
    the tree refreshes at 4 Hz with animated spinners for running agents.

    Parameters
    ----------
    show_finished : include finished/failed tasks (default: running only)
    live          : auto-refresh with animated spinners until Ctrl-C
    """
    if not RICH_AVAILABLE:
        print("rich is required: pip install rich")
        return

    from cli.tasks import get_tasks, get_task_summary

    if live:
        tick = 0
        with Live(console=console, refresh_per_second=4, screen=False) as lv:
            try:
                while True:
                    tasks = get_tasks()
                    summary = get_task_summary()
                    lv.update(
                        Group(
                            _summary_panel(summary),
                            _build_agent_tree(tasks, show_finished, tick),
                        )
                    )
                    time.sleep(0.25)
                    tick += 1
            except KeyboardInterrupt:
                pass
    else:
        tasks = get_tasks()
        summary = get_task_summary()
        console.print(_summary_panel(summary))
        console.print(_build_agent_tree(tasks, show_finished))
