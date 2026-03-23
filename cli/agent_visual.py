# cli/agent_visual.py
"""
Animated character visualization for Curie AI tasks and sub-agents.

Inspired by the Curie character: an elegant illustrated figure with
voluminous wavy blue hair, teal eyes, rosy cheeks, and red lips.

Main agent: large detailed portrait of Curie (multiple animation states).
Sub-agents: smaller character variants, each with a role-specific look:
  - Coding assistant → glasses (⊙)
  - Navigation       → directional eyes (◀ ▶)
  - Scheduler        → clock / formal look (● ●)
  - Trip planner     → sunglasses (▬═▬)
  - LLM inference    → sparkle eyes (✦ ✦)

A "spotlight" bar auto-cycles through running sub-agents, showing their
full current activity – simulating a hover/inspect effect.

Usage (via `curie tasks`):
    curie tasks --visual            # static snapshot
    curie tasks --visual --live     # animated with auto-spotlight
    curie tasks --visual --live --all  # include finished tasks
"""

from __future__ import annotations

import time
from typing import Any

try:
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich.live import Live
    from rich.align import Align
    from rich.columns import Columns
    from rich.rule import Rule
    from rich.padding import Padding
    from rich import box

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# ---------------------------------------------------------------------------
# Spinner frames for running-agent animation
# ---------------------------------------------------------------------------
_SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# ---------------------------------------------------------------------------
# Main Curie character art
# Each state is a list of (line_text, rich_style) pairs.
# Lines are ~15 visible chars wide (pad shorter ones).
# ---------------------------------------------------------------------------

# Shared hair + face top (used in every state)
_HAIR = [
    ("    )≋≋≋≋≋(    ", "bold bright_blue"),
    ("  )≋≋≋≋≋≋≋≋≋(  ", "bold bright_blue"),
    (" (≋≋≋≋≋≋≋≋≋≋≋) ", "bold bright_blue"),
]
_SHIRT = [
    (" ╲_____________╱ ", "bold bright_white"),
    ("  │░░░░░░░░░░░│  ", "bold blue"),
    ("   ╰────Curie╯   ", "bold bright_white"),
]

_CURIE_STATES: dict[str, list[tuple[str, str]]] = {
    # ── Idle / default ──────────────────────────────────────────────────
    "idle": _HAIR + [
        (" │  ━━    ━━  │ ", "bold white"),
        (" │  ◉    ◉   │ ", "bold bright_cyan"),
        (" │   ˘  ·  ˘  │ ", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─────   │ ", "bold red"),
    ] + _SHIRT,

    # ── Thinking (question mark floats to the right) ─────────────────────
    "think_1": _HAIR + [
        (" │  ━━    ━━  │?", "bold white"),
        (" │  ◔    ◔   │ ", "bold bright_cyan"),
        (" │   ˘  ·  ˘  │ ", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─ · ─   │ ", "bold white"),
    ] + _SHIRT,

    "think_2": _HAIR + [
        (" │  ━━    ━━  │ ", "bold white"),
        (" │  ◔    ◔   │?", "bold bright_cyan"),
        (" │   ˘  ·  ˘  │ ", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─ · ─   │ ", "bold white"),
    ] + _SHIRT,

    "think_3": _HAIR + [
        (" │  ━━    ━━  │ ", "bold white"),
        (" │  ◔    ◔   │ ", "bold bright_cyan"),
        (" │   ˘  ·  ˘  │?", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─ · ─   │ ", "bold white"),
    ] + _SHIRT,

    # ── Working (sparkles animate) ────────────────────────────────────────
    "work_1": [
        ("✦   )≋≋≋≋≋(   ✦", "bold bright_blue"),
        ("  )≋≋≋≋≋≋≋≋≋(  ", "bold bright_blue"),
        (" (≋≋≋≋≋≋≋≋≋≋≋) ", "bold bright_blue"),
    ] + [
        (" │  ━━    ━━  │ ", "bold white"),
        (" │  ◕    ◕   │ ", "bold bright_cyan"),
        (" │   ˘  ·  ˘  │ ", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─────   │ ", "bold red"),
    ] + _SHIRT,

    "work_2": [
        ("    )≋≋≋≋≋(    ", "bold bright_blue"),
        ("✦ )≋≋≋≋≋≋≋≋≋( ✦", "bold bright_blue"),
        (" (≋≋≋≋≋≋≋≋≋≋≋) ", "bold bright_blue"),
    ] + [
        (" │  ━━    ━━  │ ", "bold white"),
        (" │  ◕    ◕   │ ", "bold bright_cyan"),
        (" │   ˘  ·  ˘  │✦", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─────   │ ", "bold red"),
    ] + _SHIRT,

    # ── Done / happy ──────────────────────────────────────────────────────
    "done": _HAIR + [
        (" │  ─────────  │ ", "bold white"),
        (" │  ◠    ◠   │ ", "bold bright_cyan"),
        (" │   ◡  ·  ◡  │ ", "bold red dim"),   # blush + happy
        (" │     ▿      │ ", "white"),
        (" │  ─◡◡◡◡◡─  │ ", "bold red"),        # big smile
    ] + _SHIRT,

    # ── Failed / sad ──────────────────────────────────────────────────────
    "failed": _HAIR + [
        (" │  ─────────  │ ", "bold white"),
        (" │  ╌    ╌   │ ", "bold white dim"),
        (" │   ˘  ·  ˘  │ ", "white"),
        (" │     ▿      │ ", "white"),
        (" │   ─ ‸ ─   │ ", "bold white dim"),   # sad mouth
    ] + _SHIRT,
}

# Animation sequences per task status
_CURIE_ANIM: dict[str, list[str]] = {
    "running": ["think_1", "think_2", "think_3", "idle"],
    "working": ["work_1", "work_2", "work_1", "idle"],
    "done":    ["done"],
    "failed":  ["failed"],
}


def _curie_frame(status: str, tick: int) -> list[tuple[str, str]]:
    """Return the current animation frame for the main Curie agent."""
    seq = _CURIE_ANIM.get(status, _CURIE_ANIM["running"])
    key = seq[tick % len(seq)]
    return _CURIE_STATES[key]


def _render_curie(status: str, tick: int) -> "Text":
    """Render the main Curie character as a Rich Text object."""
    lines = _curie_frame(status, tick)
    t = Text(justify="center")
    for line_text, style in lines:
        t.append(line_text + "\n", style=style)
    return t


# ---------------------------------------------------------------------------
# Sub-agent character art
# Each entry: list of (line_text, idle_style, active_style, done_style)
# ---------------------------------------------------------------------------

def _sub_lines(
    hair_style: str,
    eye_l: str,
    eye_r: str,
    badge: str,
    mouth_idle: str = " ─ ",
    mouth_active: str = " ⌢ ",
    mouth_done: str = " ◡ ",
) -> dict[str, list[tuple[str, str]]]:
    """Build state-keyed art for a sub-agent character."""
    _h = [
        ("  )≋≋≋(  ", hair_style),
        (" (≋≋≋≋≋) ", hair_style),
    ]
    return {
        "idle": _h + [
            (f" │{eye_l}   {eye_r}│ ", "bold bright_white"),
            (f" │{mouth_idle}│ ", "white"),
            (f" ╰──{badge}──╯ ", "bold"),
        ],
        "running": _h + [
            (f" │{eye_l}   {eye_r}│ ", "bold green"),
            (f" │{mouth_active}│ ", "green"),
            (f" ╰──{badge}──╯ ", "bold green"),
        ],
        "done": _h + [
            (f" │{eye_l}   {eye_r}│ ", "bold cyan"),
            (f" │{mouth_done}│ ", "cyan"),
            (f" ╰──{badge}──╯ ", "bold cyan"),
        ],
        "skipped": _h + [
            (f" │╌   ╌│ ", "dim"),
            (f" │ ─ │ ", "dim"),
            (f" ╰──{badge}──╯ ", "dim"),
        ],
        "failed": _h + [
            (f" │{eye_l}   {eye_r}│ ", "bold red"),
            (f" │ ‸ │ ", "bold red"),
            (f" ╰──{badge}──╯ ", "bold red"),
        ],
    }


# Role-specific character definitions
_SUB_CHARS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "coding_assistant": _sub_lines(
        "bright_blue", "⊙", "⊙", "💻",
        mouth_active=" ⌢ ", mouth_done=" ◡ "
    ),
    "navigation": _sub_lines(
        "bright_green", "◀", "▶", "🗺",
        mouth_active=" ⁻ ", mouth_done=" ◡ "
    ),
    "scheduler": _sub_lines(
        "yellow", "●", "●", "⏰",
        mouth_active=" ⌢ ", mouth_done=" ◡ "
    ),
    "trip_planner": _sub_lines(
        "bright_magenta", "▬", "▬", "✈",
        mouth_idle=" ─ ", mouth_active=" ◡ ", mouth_done=" ◡ "
    ),
    "llm_inference": _sub_lines(
        "bright_cyan", "✦", "✦", "🧠",
        mouth_active=" ~ ", mouth_done=" ◡ "
    ),
    "system_commands": _sub_lines(
        "bright_yellow", "◈", "◈", "⚙",
        mouth_active=" ─ ", mouth_done=" ◡ "
    ),
}
_DEFAULT_SUB = _sub_lines("white", "◉", "◉", "?")


def _sub_state(role: str, status: str, summary: str) -> str:
    """Map agent fields to a character art state key."""
    if status == "running":
        return "running"
    if status == "done":
        return "skipped" if summary == "skipped" else "done"
    if status == "failed":
        return "failed"
    return "idle"


def _render_sub(agent: dict[str, Any], tick: int) -> "Text":
    """Render a sub-agent as a Rich Text object (centered in its panel)."""
    role = agent.get("role", "?")
    status = agent.get("status", "idle")
    summary = agent.get("result_summary", "")
    state = _sub_state(role, status, summary)

    chars = _SUB_CHARS.get(role, _DEFAULT_SUB)
    lines = chars.get(state, chars.get("idle", []))

    # Animate running state
    if state == "running":
        spin = _SPIN[tick % len(_SPIN)]
        lines = lines.copy()
        if lines:
            first_text, first_style = lines[0]
            lines[0] = (f"{spin} {first_text.strip()} {spin}", first_style)

    t = Text(justify="center")
    for line_text, style in lines:
        t.append(line_text + "\n", style=style)
    return t


# ---------------------------------------------------------------------------
# Duration helpers (copied to avoid cross-import)
# ---------------------------------------------------------------------------

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
    if started is None:
        return "—"
    end = finished or time.time()
    secs = int(end - started)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


# ---------------------------------------------------------------------------
# Layout builders
# ---------------------------------------------------------------------------

_STATUS_COLOR = {
    "running": "green",
    "done":    "cyan",
    "failed":  "red",
    "cancelled": "yellow",
}
_STATUS_ICON = {
    "running": "⚡",
    "done":    "✅",
    "failed":  "❌",
    "cancelled": "⚠️",
}


def _task_status_color(task: dict[str, Any]) -> str:
    return _STATUS_COLOR.get(task.get("status", ""), "white")


def _build_main_panel(task: dict[str, Any] | None, tick: int) -> "Panel":
    """Render the large main-Curie panel for the given task."""
    if task is None:
        art = _render_curie("idle", tick)
        subtitle = "[dim]Waiting for messages…[/dim]"
        title = "✨ Curie"
    else:
        t_status = task.get("status", "running")
        art = _render_curie(t_status, tick)
        t_age = _age(task.get("started_at"))
        t_channel = task.get("channel", "?")
        t_desc = (task.get("description") or "")[:55]
        col = _task_status_color(task)
        icon = _STATUS_ICON.get(t_status, "")
        title = f"{icon} [{col} bold]Curie[/{col} bold]"
        subtitle = (
            f"[{col}]{t_desc}[/{col}]  "
            f"[dim]#{task.get('id','?')[:8]} · {t_channel} · {t_age}[/dim]"
        )

    return Panel(
        Align.center(art),
        title=title,
        subtitle=subtitle,
        box=box.ROUNDED,
        border_style="bright_blue",
        width=22,
        padding=(0, 0),
    )


def _build_sub_panel(agent: dict[str, Any], tick: int, highlighted: bool = False) -> "Panel":
    """Render a single sub-agent as a character panel."""
    role = agent.get("role", "?")
    status = agent.get("status", "idle")
    summary = agent.get("result_summary", "")
    description = agent.get("description", "")
    ag_dur = _dur(agent.get("started_at"), agent.get("finished_at"))

    # Title = role label
    _ROLE_TITLES = {
        "coding_assistant": "💻 Coding",
        "navigation":       "🗺  Nav",
        "scheduler":        "⏰ Sched.",
        "trip_planner":     "✈  Trip",
        "llm_inference":    "🧠 LLM",
        "system_commands":  "⚙  Sys",
    }
    title = _ROLE_TITLES.get(role, f"🔧 {role[:8]}")

    # Subtitle = status + duration
    col = _STATUS_COLOR.get(status, "white")
    icon = _STATUS_ICON.get(status, "❓")
    if status == "done" and summary == "skipped":
        subtitle = "[dim]skipped[/dim]"
        border = "dim"
    elif status == "running":
        spin = _SPIN[tick % len(_SPIN)]
        subtitle = f"[green]{spin} {ag_dur}[/green]"
        border = "bright_green" if highlighted else "green"
    elif status == "done":
        subtitle = f"[cyan]✔ {ag_dur}[/cyan]"
        border = "cyan"
    elif status == "failed":
        subtitle = f"[red]✖ {ag_dur}[/red]"
        border = "red"
    else:
        subtitle = f"[dim]{ag_dur}[/dim]"
        border = "dim"

    art = _render_sub(agent, tick)

    return Panel(
        Align.center(art),
        title=f"[bold]{title}[/bold]",
        subtitle=subtitle,
        box=box.HEAVY if highlighted else box.ROUNDED,
        border_style=border,
        width=16,
        padding=(0, 0),
    )


def _build_spotlight(agent: dict[str, Any] | None, tick: int) -> "Panel":
    """Render the spotlight bar describing what the highlighted agent is doing."""
    if agent is None:
        return Panel(
            "[dim italic]No active sub-agents[/dim italic]",
            title="🔍 Spotlight",
            box=box.MINIMAL,
            border_style="dim",
        )

    role = agent.get("role", "?")
    status = agent.get("status", "idle")
    activity = agent.get("description") or agent.get("result_summary") or "(no details)"
    ag_dur = _dur(agent.get("started_at"), agent.get("finished_at"))
    col = _STATUS_COLOR.get(status, "white")
    spin = _SPIN[tick % len(_SPIN)] if status == "running" else _STATUS_ICON.get(status, "")

    content = Text()
    content.append(f"{spin}  ", style=f"bold {col}")
    content.append(f"{role}  ", style=f"bold {col}")
    content.append(activity, style=f"{col}")
    content.append(f"  [{ag_dur}]", style="dim")

    return Panel(
        content,
        title="🔍 Spotlight  [dim](hover / inspect)[/dim]",
        box=box.MINIMAL,
        border_style="bright_blue",
    )


def _build_no_tasks_panel() -> "Panel":
    art = _render_curie("idle", 0)
    return Panel(
        Group(
            Align.center(art),
            Align.center(Text("\n[dim italic]No active tasks – waiting…[/dim italic]")),
        ),
        title="✨ [bold bright_blue]Curie AI[/bold bright_blue]",
        box=box.ROUNDED,
        border_style="bright_blue",
    )


def _build_visual(
    tasks: list[dict[str, Any]],
    show_finished: bool,
    tick: int,
    spotlight_idx: int,
) -> "Group":
    """Compose the full visual layout as a Rich renderable Group."""
    visible = [t for t in tasks if show_finished or t.get("status") == "running"]

    if not visible:
        return Group(_build_no_tasks_panel())

    items: list[Any] = []

    for task in visible:
        t_status = task.get("status", "running")
        sub_agents = list(task.get("sub_agents", {}).values())
        running_agents = [a for a in sub_agents if a.get("status") == "running"]

        # Determine which sub-agent is spotlighted
        spotlight_agent: dict[str, Any] | None = None
        if running_agents:
            spotlight_agent = running_agents[spotlight_idx % len(running_agents)]
        elif sub_agents:
            spotlight_agent = sub_agents[spotlight_idx % len(sub_agents)]

        # Build the row: main Curie + sub-agents side by side
        row_panels: list[Any] = [_build_main_panel(task, tick)]
        for agent in sub_agents:
            highlighted = (
                spotlight_agent is not None
                and agent.get("id") == spotlight_agent.get("id")
            )
            row_panels.append(_build_sub_panel(agent, tick, highlighted=highlighted))

        items.append(Columns(row_panels, equal=False, expand=False))
        items.append(_build_spotlight(spotlight_agent, tick))
        items.append(Rule(style="dim"))

    return Group(*items)


def _summary_line(tasks: list[dict[str, Any]]) -> str:
    total = len(tasks)
    running = sum(1 for t in tasks if t.get("status") == "running")
    total_agents = sum(len(t.get("sub_agents", {})) for t in tasks)
    running_agents = sum(
        sum(1 for a in t.get("sub_agents", {}).values() if a.get("status") == "running")
        for t in tasks
    )
    return (
        f"  [bold]Tasks:[/bold] [green]{running}[/green] running / {total} total"
        f"    [bold]Sub-agents:[/bold] [green]{running_agents}[/green] running / {total_agents} total"
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def show_visual(show_finished: bool = False, live: bool = False) -> None:
    """
    Display the animated character visualization.

    Each task shows the main Curie agent alongside role-specific sub-agent
    characters.  A spotlight bar highlights the currently active sub-agent,
    showing exactly what it is doing – analogous to hovering over it.

    Parameters
    ----------
    show_finished : include done/failed tasks (default: running only)
    live          : animate and auto-cycle the spotlight every ~2 s
    """
    if not RICH_AVAILABLE:
        print("rich is required: pip install rich")
        return

    from cli.tasks import get_tasks

    if live:
        tick = 0
        spotlight_idx = 0
        spotlight_timer = time.time()
        spotlight_interval = 2.5  # seconds per highlighted sub-agent

        with Live(console=console, refresh_per_second=4, screen=False) as lv:
            try:
                while True:
                    tasks = get_tasks()
                    now = time.time()

                    # Advance spotlight every spotlight_interval seconds
                    if now - spotlight_timer >= spotlight_interval:
                        spotlight_idx += 1
                        spotlight_timer = now

                    summary = _summary_line(tasks)
                    lv.update(
                        Group(
                            Panel(summary, box=box.MINIMAL, border_style="bright_blue"),
                            _build_visual(tasks, show_finished, tick, spotlight_idx),
                        )
                    )
                    time.sleep(0.25)
                    tick += 1
            except KeyboardInterrupt:
                pass
    else:
        tasks = get_tasks()
        console.print(
            Panel(_summary_line(tasks), box=box.MINIMAL, border_style="bright_blue")
        )
        console.print(_build_visual(tasks, show_finished, tick=0, spotlight_idx=0))
