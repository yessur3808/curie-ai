# cli/help_cmd.py
"""
`curie help` – full Rich-formatted command reference.

Prints every curie command grouped by category with a one-liner description
and common usage examples, styled with Rich panels and tables.
"""

from __future__ import annotations

import platform
import sys

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text
    from rich.rule import Rule
    from rich import box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None

_VERSION = "0.1.0"
_OS = platform.system()

# ─── Command catalogue ────────────────────────────────────────────────────────
# Each entry: (command, description, [examples...])

_COMMANDS: list[tuple[str, str, list[str]]] = [
    # Daemon & Process
    ("start [--api|--telegram|--discord|--all]",
     "Start Curie daemon in the background",
     ["curie start", "curie start --api --telegram", "curie start --all"]),

    ("stop",
     "Gracefully stop the running daemon",
     ["curie stop"]),

    ("restart [--api|--telegram|--discord|--all]",
     "Stop and restart the daemon",
     ["curie restart", "curie restart --all"]),

    ("status",
     "Show daemon PID, uptime, and log path",
     ["curie status"]),

    ("logs [-n N] [-f]",
     "Show the last N daemon log lines; -f to follow",
     ["curie logs", "curie logs -n 100", "curie logs -f"]),

    # Agent / Chat
    ("agent",
     "Start an interactive chat session with Curie",
     ["curie agent"]),

    ("agent -m \"message\"",
     "Send a single message to Curie and print the reply",
     ["curie agent -m \"What is the weather today?\""]),

    # Setup & Diagnostics
    ("onboard",
     "Guided first-time setup wizard (API keys, DB, connectors)",
     ["curie onboard"]),

    ("doctor [--verbose]",
     "Run full system diagnostics – dependencies, env vars, connectivity",
     ["curie doctor", "curie doctor --verbose"]),

    # System Metrics & Tasks
    ("metrics [--once] [--interval SECS]",
     "Live dashboard: CPU, RAM, Disk, Network, GPU (requires psutil)",
     ["curie metrics", "curie metrics --once", "curie metrics --interval 0.5"]),

    ("tasks [--live] [--all]",
     "Show active tasks and sub-agent breakdown",
     ["curie tasks", "curie tasks --live", "curie tasks --all"]),

    # Channels
    ("channel list",
     "List configured channels with bot identity and status",
     ["curie channel list"]),

    ("channel doctor",
     "Check connectivity for each configured channel",
     ["curie channel doctor"]),

    ("channel bind-telegram TOKEN",
     "Store a Telegram bot token in .env and enable the connector",
     ["curie channel bind-telegram 123456:ABCdef…"]),

    ("channel bind-discord TOKEN",
     "Store a Discord bot token in .env and enable the connector",
     ["curie channel bind-discord ODY…"]),

    # Cron / Scheduling
    ("cron list",
     "List all scheduled prompt jobs",
     ["curie cron list"]),

    ("cron add SCHEDULE --prompt TEXT",
     "Add a new scheduled job.  SCHEDULE is a 5-field cron expression or "
     "a macro: @hourly, @daily, @weekly, @monthly, @reboot, @every_5m, @every_2h",
     [
         "curie cron add '*/5 * * * *' --prompt 'Check system health'",
         "curie cron add '@daily' --prompt 'Summarise the news'",
         "curie cron add '@every_30m' --prompt 'Are any services down?'",
     ]),

    ("cron remove ID",
     "Remove a scheduled job by its ID",
     ["curie cron remove health-check"]),

    ("cron enable|disable ID",
     "Enable or disable a job without removing it",
     ["curie cron enable health-check", "curie cron disable daily-news"]),

    # Memory
    ("memory list [--limit N]",
     "List all users with stored facts and a preview of key names",
     ["curie memory list", "curie memory list --limit 50"]),

    ("memory keys [--user ID]",
     "List every memory key name (with value preview) for a user",
     ["curie memory keys", "curie memory keys --user 3f2a1b0c-…"]),

    ("memory get KEY [--user ID]",
     "Print the full value of a stored memory key",
     ["curie memory get hobby", "curie memory get timezone --user 3f2a1b0c-…"]),

    ("memory stats",
     "Show aggregate stats: total users, profiles, facts, sessions",
     ["curie memory stats"]),

    # Auth / LLM Providers
    ("auth login --provider NAME [--key KEY]",
     "Store an API key for an LLM provider (openai, anthropic, gemini, llama.cpp)",
     ["curie auth login --provider openai", "curie auth login --provider gemini --key AIza…"]),

    ("auth status",
     "Show configured LLM providers and active priority",
     ["curie auth status"]),

    ("auth use --provider NAME",
     "Set the active LLM provider priority",
     ["curie auth use --provider anthropic"]),

    # Hardware / Peripherals
    ("hardware discover",
     "Scan for connected devices: USB, serial, audio, cameras, Bluetooth, input, network",
     ["curie hardware discover"]),

    ("peripheral list [--fresh]",
     "List previously discovered peripherals from cache; --fresh re-scans",
     ["curie peripheral list", "curie peripheral list --fresh"]),

    # OS Service
    ("service install",
     "Install Curie as an OS background service (systemd on Linux, launchd on macOS)",
     ["curie service install"]),

    ("service start|stop|restart|status",
     "Control the installed OS service",
     [
         "curie service start",
         "curie service stop",
         "curie service restart",
         "curie service status",
     ]),

    # Shell Completions
    ("completions bash|zsh|fish",
     "Print a shell completion script for the chosen shell",
     [
         "source <(curie completions bash)",
         "curie completions zsh > ~/.zfunc/_curie",
         "curie completions fish > ~/.config/fish/completions/curie.fish",
     ]),

    # Help
    ("help",
     "Show this full command reference",
     ["curie help"]),
]

# Group definitions: (heading, icon, command-prefix-list)
_GROUPS: list[tuple[str, str, list[str]]] = [
    ("Daemon & Process",    "⚙️ ",  ["start", "stop", "restart", "status", "logs"]),
    ("Agent / Chat",        "💬",  ["agent"]),
    ("Setup & Diagnostics", "🩺",  ["onboard", "doctor"]),
    ("System Metrics",      "📊",  ["metrics", "tasks"]),
    ("Channels",            "📡",  ["channel"]),
    ("Cron / Scheduling",   "⏰",  ["cron"]),
    ("Memory",              "🧠",  ["memory"]),
    ("Auth / LLM",          "🔑",  ["auth"]),
    ("Hardware / Peripherals", "🔌", ["hardware", "peripheral"]),
    ("OS Service",          "🖥️ ",  ["service"]),
    ("Shell Completions",   "🐚",  ["completions"]),
    ("Help",                "📖",  ["help"]),
]


# ─── Renderer ─────────────────────────────────────────────────────────────────

def print_full_help() -> int:
    """Print the full command reference and return 0."""

    if not _RICH:
        _plain_help()
        return 0

    _console.print()
    _console.print(Panel(
        f"[bold cyan]Curie AI[/bold cyan]  [dim]v{_VERSION}[/dim]\n\n"
        "[white]Curie is a conversational AI assistant with connectors, scheduling,\n"
        "memory, and multi-provider LLM support.  All commands work at the\n"
        "terminal[/white] [dim]and[/dim] [white]via chat on any connected platform\n"
        "(Telegram, Discord, REST API).[/white]",
        title="[bold]Welcome to Curie AI[/bold]",
        border_style="cyan",
        padding=(1, 4),
    ))

    # Build lookup: prefix → list of (command, description, examples)
    prefix_map: dict[str, list[tuple[str, str, list[str]]]] = {}
    for cmd, desc, examples in _COMMANDS:
        prefix = cmd.split()[0]
        prefix_map.setdefault(prefix, []).append((cmd, desc, examples))

    for heading, icon, prefixes in _GROUPS:
        _console.print(Rule(f"{icon}  {heading}", style="bold cyan"))

        table = Table(
            box=box.SIMPLE,
            show_header=False,
            expand=True,
            padding=(0, 2),
        )
        table.add_column("Command", style="bold green", min_width=42, no_wrap=True)
        table.add_column("Description", style="white", min_width=44)

        for prefix in prefixes:
            for cmd, desc, _ in prefix_map.get(prefix, []):
                table.add_row(f"curie {cmd}", desc)

        _console.print(table)

        # Print examples (collapsed, not repeated for each command)
        all_examples: list[str] = []
        for prefix in prefixes:
            for _, _, examples in prefix_map.get(prefix, []):
                all_examples.extend(examples[:2])  # max 2 per command
        if all_examples:
            eg_text = "  ".join(f"[dim]{e}[/dim]" for e in all_examples[:4])
            _console.print(f"  [dim]e.g.[/dim] {eg_text}\n")

    _console.print()
    _console.print(Panel(
        "[white]Chat triggers:[/white]  Every command above can also be issued naturally "
        "in any chat connector:\n\n"
        "  [bold green]\"show me the system metrics\"[/bold green]  [dim]→[/dim]  [bold]curie metrics[/bold]\n"
        "  [bold green]\"add a cron job every day to summarise news\"[/bold green]  [dim]→[/dim]  "
        "[bold]curie cron add @daily --prompt …[/bold]\n"
        "  [bold green]\"list my memory keys\"[/bold green]  [dim]→[/dim]  [bold]curie memory keys[/bold]\n"
        "  [bold green]\"discover connected devices\"[/bold green]  [dim]→[/dim]  [bold]curie hardware discover[/bold]",
        title="[bold]Natural-Language Chat Interface[/bold]",
        border_style="dim cyan",
        padding=(1, 4),
    ))
    _console.print()

    return 0


def _plain_help() -> None:
    """Fallback plain-text help when rich is not installed."""
    width = 80
    print()
    print("=" * width)
    print("  Curie AI – Full Command Reference")
    print("=" * width)

    prefix_map: dict[str, list[tuple[str, str, list[str]]]] = {}
    for cmd, desc, examples in _COMMANDS:
        prefix = cmd.split()[0]
        prefix_map.setdefault(prefix, []).append((cmd, desc, examples))

    for heading, icon, prefixes in _GROUPS:
        print(f"\n{icon}  {heading}")
        print("-" * 50)
        for prefix in prefixes:
            for cmd, desc, examples in prefix_map.get(prefix, []):
                print(f"  curie {cmd:<40} {desc}")
                for ex in examples[:1]:
                    print(f"    example: {ex}")
    print()
