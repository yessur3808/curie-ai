# cli/canvas.py
"""
Canvas CLI commands for Curie AI.

Manages the live agent-driven visual workspace persisted to
``~/.curie/canvas.json``.

Commands:
  curie canvas list              – show all canvas nodes
  curie canvas add TITLE --content TEXT [--type TYPE]
  curie canvas remove ID         – remove a node by ID
  curie canvas clear             – remove all nodes
  curie canvas open              – open live canvas in browser
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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


# ── Canvas CRUD (thin wrappers around agent.tools.canvas) ─────────────────────


def _get_canvas():
    """Lazy import of the canvas tool module."""
    from agent.tools import canvas as _canvas  # noqa: PLC0415
    return _canvas


# ── Public command handlers ───────────────────────────────────────────────────


def cmd_canvas_list() -> int:
    """List all canvas nodes."""
    canvas = _get_canvas()
    nodes = canvas.get_nodes()

    if not nodes:
        _p("[yellow]Canvas is empty.[/yellow]")
        _p("  Add a node with: [bold]curie canvas add 'My note' --content 'Hello world'[/bold]")
        return 0

    if _RICH:
        table = Table(
            title="Curie AI – Live Canvas",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("ID", style="bold", min_width=18)
        table.add_column("Type", min_width=8)
        table.add_column("Title", min_width=24)
        table.add_column("Content preview", min_width=40)

        for node in nodes:
            preview = (node["content"][:55] + "…") if len(node["content"]) > 56 else node["content"]
            table.add_row(
                node["id"],
                node["type"],
                node["title"],
                preview,
            )
        _console.print(table)
    else:
        print(f"{'ID':<22} {'Type':<10} {'Title':<28} {'Content'}")
        print("-" * 80)
        for node in nodes:
            preview = (node["content"][:30] + "…") if len(node["content"]) > 31 else node["content"]
            print(f"{node['id']:<22} {node['type']:<10} {node['title']:<28} {preview}")

    return 0


def cmd_canvas_add(title: str, content: str, node_type: str = "text") -> int:
    """Add a node to the canvas."""
    canvas = _get_canvas()
    node = canvas.add_node(title, content, node_type=node_type, author="cli")
    _p(f"[green]✓[/green] Canvas node [bold]{node['id']}[/bold] added (type: {node_type}).")
    return 0


def cmd_canvas_remove(node_id: str) -> int:
    """Remove a canvas node."""
    canvas = _get_canvas()
    ok = canvas.remove_node(node_id)
    if ok:
        _p(f"[green]✓[/green] Canvas node [bold]{node_id}[/bold] removed.")
        return 0
    _p(f"[red]✗[/red] No canvas node with id [bold]{node_id}[/bold] found.")
    return 1


def cmd_canvas_clear() -> int:
    """Remove all canvas nodes."""
    canvas = _get_canvas()
    count = canvas.clear_canvas()
    _p(f"[green]✓[/green] Canvas cleared ({count} node{'s' if count != 1 else ''} removed).")
    return 0


def cmd_canvas_open() -> int:
    """Open the live canvas in a browser."""
    from cli.agent_webview import show_web  # noqa: PLC0415
    show_web(show_finished=False, canvas_mode=True)
    return 0


def cmd_canvas(args) -> int:
    """Dispatch canvas subcommands."""
    action = getattr(args, "canvas_action", None)

    if action == "list" or action is None:
        return cmd_canvas_list()
    elif action == "add":
        return cmd_canvas_add(
            args.title,
            args.content,
            node_type=getattr(args, "type", "text"),
        )
    elif action == "remove":
        return cmd_canvas_remove(args.node_id)
    elif action == "clear":
        return cmd_canvas_clear()
    elif action == "open":
        return cmd_canvas_open()
    else:
        _p(f"[red]Unknown canvas action: {action}[/red]")
        return 1
