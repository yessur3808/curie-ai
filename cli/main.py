# cli/main.py
"""
Main entry point for the `curie` command-line interface.

Usage examples:
  curie start                 # Start Curie daemon in background
  curie stop                  # Stop the running daemon
  curie restart               # Restart the daemon
  curie status                # Show daemon status
  curie metrics               # Live system-metrics dashboard
  curie metrics --once        # One-shot metrics snapshot
  curie tasks                 # Show task / sub-agent breakdown
  curie tasks --live          # Live-updating task view
  curie tasks --all           # Include finished tasks
  curie tasks --tree          # Rich tree visualization
  curie tasks --tree --live   # Animated tree view
  curie tasks --visual        # Animated ASCII character view
  curie tasks --visual --live # Live animated characters
  curie tasks --web           # Browser-based animated Curie dashboard
  curie agent                 # Interactive chat with Curie
  curie agent -m "hello"      # Single message
  curie doctor                # System diagnostics
  curie service install       # Install as OS service
  curie service start|stop|restart|status
  curie logs [--lines N]      # Tail the daemon log
  curie onboard               # Guided first-time setup wizard
  curie channel list          # List configured channels
  curie channel doctor        # Check channel connectivity
  curie channel bind-telegram TOKEN
  curie channel bind-discord TOKEN
  curie cron list             # List scheduled prompt jobs
  curie cron add "SCHEDULE" --prompt "text"
  curie cron remove ID
  curie cron enable|disable ID
  curie memory list           # List users and memory facts
  curie memory keys           # List memory key names for master user
  curie memory get KEY        # Get a specific memory key
  curie memory stats          # Aggregate memory statistics
  curie auth login --provider openai
  curie auth status           # Show configured LLM providers
  curie auth use --provider anthropic
  curie hardware discover     # Scan for connected devices
  curie peripheral list       # List peripherals from cache
  curie peripheral list --fresh  # Re-scan and list
  curie completions bash      # Print bash completion script
  curie completions zsh       # Print zsh completion script
  curie completions fish      # Print fish completion script
  curie help                  # Full command reference
"""

from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path


# ─── sub-command handlers ─────────────────────────────────────────────────────


def _cmd_start(args: argparse.Namespace) -> int:
    from cli.daemon import start_daemon
    from cli import ui

    connector_args: list[str] = []
    if args.telegram:
        connector_args.append("--telegram")
    if args.discord:
        connector_args.append("--discord")
    if args.api:
        connector_args.append("--api")
    if args.all_connectors:
        connector_args = ["--all"]

    # Interactive connector picker if no flags were given AND we have a real TTY
    if not connector_args and sys.stdin.isatty():
        _connector_opts = ["API (HTTP REST)", "Telegram bot", "Discord bot", "All connectors"]
        _connector_flags = ["--api", "--telegram", "--discord", "--all"]
        try:
            chosen = ui.multi_select(
                _connector_opts,
                title="Select connectors to enable (Space to toggle)",
                defaults=[0],
            )
            if len(chosen) == len(_connector_opts) - 1:
                connector_args = ["--all"]
            elif chosen:
                connector_args = [_connector_flags[i] for i in chosen]
        except (KeyboardInterrupt, EOFError):
            ui.warn("Aborted.")
            return 130

    label = f"Starting Curie daemon with {connector_args or 'defaults'!s}…"
    with ui.spinner(label):
        result = start_daemon(connector_args=connector_args if connector_args else None)

    if result["success"]:
        ui.success(result["message"])
        ui.notify("Curie AI", result["message"])
    else:
        ui.error(result["message"])
    return 0 if result["success"] else 1


def _cmd_stop(args: argparse.Namespace) -> int:
    from cli.daemon import stop_daemon
    from cli import ui
    with ui.spinner("Stopping Curie daemon…"):
        result = stop_daemon()
    if result["success"]:
        ui.success(result["message"])
        ui.notify("Curie AI", result["message"])
    else:
        ui.error(result["message"])
    return 0 if result["success"] else 1


def _cmd_restart(args: argparse.Namespace) -> int:
    from cli.daemon import restart_daemon
    from cli import ui
    connector_args: list[str] = []
    if args.telegram:
        connector_args.append("--telegram")
    if args.discord:
        connector_args.append("--discord")
    if args.api:
        connector_args.append("--api")
    if args.all_connectors:
        connector_args = ["--all"]
    with ui.spinner("Restarting Curie daemon…"):
        result = restart_daemon(connector_args=connector_args if connector_args else None)
    if result["success"]:
        ui.success(result["message"])
        ui.notify("Curie AI", result["message"])
    else:
        ui.error(result["message"])
    return 0 if result["success"] else 1


def _cmd_status(args: argparse.Namespace) -> int:
    from cli.daemon import get_status

    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console()

        st = get_status()
        table = Table(box=box.ROUNDED, show_header=False, expand=False)
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        table.add_row("Status", "[green]running[/green]" if st["running"] else "[red]stopped[/red]")
        if st["pid"]:
            table.add_row("PID", str(st["pid"]))
        if st.get("uptime_seconds") is not None:
            secs = st["uptime_seconds"]
            uptime = f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s"
            table.add_row("Uptime", uptime)
        table.add_row("Log file", st["log_file"])
        console.print(table)
    except ImportError:
        st = get_status()
        print(f"Status: {'running' if st['running'] else 'stopped'}")
        if st["pid"]:
            print(f"PID: {st['pid']}")
        print(f"Log: {st['log_file']}")

    return 0 if get_status()["running"] else 1


def _cmd_metrics(args: argparse.Namespace) -> int:
    from cli.metrics import show_metrics_live, show_metrics_once
    if args.once:
        show_metrics_once()
    else:
        show_metrics_live(refresh_rate=args.interval)
    return 0


def _cmd_tasks(args: argparse.Namespace) -> int:
    if getattr(args, "canvas", False):
        from cli.canvas_webview import show_canvas
        show_canvas(show_finished=args.all)
        return 0
    if getattr(args, "web", False):
        from cli.agent_webview import show_web
        show_web(show_finished=args.all)
        return 0
    from cli.tasks_display import show_tasks
    show_tasks(
        show_finished=args.all,
        live=args.live,
        tree=getattr(args, "tree", False),
        visual=getattr(args, "visual", False),
    )
    return 0


def _cmd_canvas(args: argparse.Namespace) -> int:
    from cli.canvas_webview import show_canvas
    show_canvas(show_finished=getattr(args, "all", False))
    return 0


def _cmd_sessions(args: argparse.Namespace) -> int:
    """Manage and inspect conversation sessions."""
    from cli import ui
    sub = args.sessions_action

    try:
        from memory.session_store import get_session_manager
        sm = get_session_manager()
    except Exception as exc:
        ui.error(f"Could not connect to session store: {exc}")
        return 1

    if sub == "list":
        channel = getattr(args, "channel", None)
        try:
            sessions = sm.list_sessions(channel=channel)
        except Exception as exc:
            ui.error(f"Failed to list sessions: {exc}")
            return 1
        if not sessions:
            ui.warn("No sessions found.")
            return 0
        try:
            from rich.console import Console
            from rich.table import Table
            from rich import box
            console = Console()
            table = Table(box=box.SIMPLE_HEAVY, show_header=True)
            table.add_column("Session key", style="cyan")
            table.add_column("Channel", style="dim")
            table.add_column("User ID", style="dim")
            table.add_column("Messages", justify="right")
            table.add_column("Updated")
            for s in sessions:
                table.add_row(
                    str(s.get("_id", "")),
                    s.get("channel", ""),
                    s.get("user_id", ""),
                    str(len(s.get("messages", []))),
                    str(s.get("updated_at", "")[:19] if s.get("updated_at") else ""),
                )
            console.print(table)
        except ImportError:
            for s in sessions:
                print(s.get("_id", ""), s.get("channel", ""), s.get("user_id", ""))
        return 0

    if sub == "clear":
        user_id = getattr(args, "user_id", None)
        channel = getattr(args, "channel", None)
        if not user_id:
            ui.error("Provide --user-id to identify the session to clear.")
            return 1
        try:
            if channel:
                sm.reset_session(channel, user_id)
                ui.success(f"Cleared session for {channel}:{user_id}")
            else:
                sm.reset_user_all_channels(user_id)
                ui.success(f"Cleared all sessions for user {user_id}")
        except Exception as exc:
            ui.error(f"Failed to clear session: {exc}")
            return 1
        return 0

    if sub == "stats":
        try:
            all_sessions = sm.list_sessions()
        except Exception as exc:
            ui.error(f"Failed to fetch sessions: {exc}")
            return 1
        total = len(all_sessions)
        total_msgs = sum(len(s.get("messages", [])) for s in all_sessions)
        channels: dict = {}
        for s in all_sessions:
            ch = s.get("channel", "unknown")
            channels[ch] = channels.get(ch, 0) + 1
        try:
            from rich.console import Console
            from rich.table import Table
            from rich import box
            console = Console()
            console.print(f"\n[bold cyan]Session Statistics[/bold cyan]")
            console.print(f"  Total sessions : [bold]{total}[/bold]")
            console.print(f"  Total messages : [bold]{total_msgs}[/bold]")
            console.print(f"  By channel:")
            for ch, cnt in sorted(channels.items()):
                console.print(f"    {ch}: {cnt}")
            console.print()
        except ImportError:
            print(f"Total sessions: {total}, messages: {total_msgs}")
        return 0

    ui.error(f"Unknown sessions action: {sub!r}")
    return 1


def _cmd_tools(args: argparse.Namespace) -> int:
    """List and inspect available first-class tools."""
    from cli import ui

    category = getattr(args, "category", None)
    tag = getattr(args, "tag", None)
    available_only = getattr(args, "available_only", False)

    try:
        from agent.tools import list_tools
        tools = list_tools(available_only=available_only, category=category, tag=tag)
    except Exception as exc:
        ui.error(f"Could not load tools registry: {exc}")
        return 1

    if not tools:
        ui.warn("No tools found matching the given filters.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        console = Console()
        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            title="[bold cyan]Curie AI — First-class Tools[/bold cyan]",
        )
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Display Name")
        table.add_column("Category", style="dim")
        table.add_column("Status", justify="center")
        table.add_column("Description")
        for t in tools:
            status = "[green]✓[/green]" if t.available else "[red]✗[/red]"
            table.add_row(
                t.name,
                t.display_name,
                t.category,
                status,
                t.description[:60] + ("…" if len(t.description) > 60 else ""),
            )
        console.print(table)
        avail = sum(1 for t in tools if t.available)
        console.print(
            f"  [dim]{avail}/{len(tools)} available "
            f"({'--available' if not available_only else 'all'} filter)[/dim]\n"
        )
    except ImportError:
        for t in tools:
            mark = "✓" if t.available else "✗"
            print(f"[{mark}] {t.name:<24} ({t.category}) — {t.description[:60]}")
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    from cli.agent_cmd import run_single_message, run_interactive_chat
    api_url = args.api_url or "http://127.0.0.1:8000"
    if args.message:
        run_single_message(args.message, use_api=not args.no_api, api_url=api_url)
    else:
        run_interactive_chat(use_api=not args.no_api, api_url=api_url)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from cli.doctor import run_doctor
    return run_doctor(verbose=args.verbose)


def _cmd_service(args: argparse.Namespace) -> int:
    from cli.service import install_service, service_action
    action = args.service_action
    if action == "install":
        return install_service()
    return service_action(action)


def _cmd_logs(args: argparse.Namespace) -> int:
    from cli.daemon import LOG_FILE
    from cli import ui

    n = args.lines
    if not LOG_FILE.exists():
        ui.error(f"Log file not found: {LOG_FILE}")
        return 1

    if args.follow:
        # Rich live tail (press Ctrl-C to exit)
        ui.live_tail(LOG_FILE, n_lines=n, label="Curie Daemon Log")
        return 0

    # Plain one-shot print
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
        tail = lines[-n:]
    except OSError as e:
        ui.error(f"Could not read log: {e}")
        return 1

    try:
        from rich.console import Console
        from rich.text import Text
        con = Console()
        for line in tail:
            con.print(ui._colourise_log_line(line))
    except ImportError:
        for line in tail:
            print(line)
    return 0


# ── New subcommand handlers ────────────────────────────────────────────────────


def _cmd_onboard(args: argparse.Namespace) -> int:
    from cli.onboard import run_onboard
    return run_onboard(verbose=getattr(args, "verbose", False))


def _cmd_channel(args: argparse.Namespace) -> int:
    from cli.channel import cmd_channel_list, cmd_channel_doctor, cmd_channel_bind
    sub = args.channel_action
    if sub == "list":
        return cmd_channel_list()
    if sub == "doctor":
        return cmd_channel_doctor()
    if sub in ("bind-telegram", "bind-discord"):
        platform = sub.split("-")[1]  # "telegram" or "discord"
        token = getattr(args, "token", None)
        if not token:
            print(f"Usage: curie channel {sub} <TOKEN>")
            return 1
        return cmd_channel_bind(platform, token)
    print(f"Unknown channel action: {sub!r}")
    return 1


def _cmd_cron(args: argparse.Namespace) -> int:
    from cli.cron import cmd_cron_list, cmd_cron_add, cmd_cron_remove, cmd_cron_enable
    sub = args.cron_action
    if sub == "list":
        return cmd_cron_list()
    if sub == "add":
        schedule = args.schedule
        prompt = args.prompt
        if not schedule or not prompt:
            print("Usage: curie cron add <SCHEDULE> --prompt <TEXT>")
            return 1
        return cmd_cron_add(schedule, prompt)
    if sub == "remove":
        return cmd_cron_remove(args.job_id)
    if sub in ("enable", "disable"):
        return cmd_cron_enable(args.job_id, sub == "enable")
    print(f"Unknown cron action: {sub!r}")
    return 1


def _cmd_memory(args: argparse.Namespace) -> int:
    from cli.memory_cmd import (
        cmd_memory_list, cmd_memory_keys, cmd_memory_get,
        cmd_memory_stats, cmd_memory_clear_user,
    )
    sub = args.memory_action
    if sub == "list":
        return cmd_memory_list(limit=getattr(args, "limit", 20))
    if sub == "keys":
        return cmd_memory_keys(internal_id=getattr(args, "user", None))
    if sub == "get":
        return cmd_memory_get(args.key, internal_id=getattr(args, "user", None))
    if sub == "stats":
        return cmd_memory_stats()
    if sub == "clear-user":
        return cmd_memory_clear_user(args.user_id)
    print(f"Unknown memory action: {sub!r}")
    return 1


def _cmd_auth(args: argparse.Namespace) -> int:
    from cli.auth import cmd_auth_login, cmd_auth_status, cmd_auth_use
    from cli import ui
    sub = args.auth_action
    if sub == "login":
        provider = getattr(args, "provider", None)
        if not provider:
            # Interactive selector
            _providers = ["openai", "anthropic", "gemini", "llama.cpp"]
            try:
                idx = ui.select(_providers, title="Choose LLM provider to configure")
                provider = _providers[idx]
            except (KeyboardInterrupt, Exception):
                ui.warn("Aborted.")
                return 130
        return cmd_auth_login(provider, api_key=getattr(args, "key", None))
    if sub == "status":
        return cmd_auth_status()
    if sub == "use":
        provider = getattr(args, "provider", None)
        if not provider:
            _providers = ["openai", "anthropic", "gemini", "llama.cpp"]
            try:
                idx = ui.select(_providers, title="Set active LLM provider")
                provider = _providers[idx]
            except (KeyboardInterrupt, Exception):
                ui.warn("Aborted.")
                return 130
        return cmd_auth_use(provider)
    print(f"Unknown auth action: {sub!r}")
    return 1


def _cmd_completions(args: argparse.Namespace) -> int:
    from cli.completions import cmd_completions
    return cmd_completions(args.shell)


def _cmd_hardware(args: argparse.Namespace) -> int:
    from cli.hardware import cmd_hardware_discover
    sub = args.hardware_action
    if sub == "discover":
        return cmd_hardware_discover()
    print(f"Unknown hardware action: {sub!r}")
    return 1


def _cmd_peripheral(args: argparse.Namespace) -> int:
    from cli.hardware import cmd_peripheral_list
    fresh = getattr(args, "fresh", False)
    return cmd_peripheral_list(fresh=fresh)


def _cmd_help(args: argparse.Namespace) -> int:
    from cli.help_cmd import print_full_help
    return print_full_help()


# ─── parser setup ─────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="curie",
        description="Curie AI – CLI management interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  curie start                      Start Curie daemon in background
  curie start --api --telegram     Start with specific connectors
  curie stop                       Stop the daemon
  curie status                     Show daemon status
  curie metrics                    Live system metrics dashboard
  curie metrics --once             One-shot metrics snapshot
  curie tasks                      Show task / sub-agent breakdown
  curie tasks --live               Live task view
  curie tasks --tree               Tree visualization of agents
  curie tasks --tree --live        Live animated tree view
  curie tasks --visual             Animated ASCII character view
  curie tasks --visual --live      Live animated characters
  curie tasks --web                Browser-based animated Curie dashboard
  curie agent                      Interactive chat
  curie agent -m "hello"           Single message
  curie doctor                     System diagnostics
  curie service install            Install as OS service
  curie service start              Start OS service
  curie logs -n 50                 Show last 50 log lines
  curie logs -f                    Follow log output
  curie onboard                    First-time setup wizard
  curie channel list               List configured channels
  curie channel doctor             Check channel connectivity
  curie channel bind-telegram TOKEN  Set Telegram token
  curie cron list                  List scheduled jobs
  curie cron add '*/5 * * * *' --prompt 'Check health'
  curie cron remove job-id
  curie memory list                List users + memory facts
  curie memory keys                List all key names for master user
  curie memory keys --user UID     List all key names for a specific user
  curie memory get hobby           Get the value of 'hobby' key (master user)
  curie memory stats               Aggregate memory stats
  curie auth login --provider openai
  curie auth status                Show configured providers
  curie auth use --provider anthropic
  curie hardware discover          Scan for connected devices (USB, serial, audio…)
  curie peripheral list            List peripherals from cache
  curie peripheral list --fresh    Re-scan and list
  source <(curie completions bash)   Enable bash tab-completion
  curie completions zsh > ~/.zfunc/_curie
  curie help                       Full command reference with descriptions
""",
    )

    subs = parser.add_subparsers(dest="command", metavar="<command>")

    # ── start ──────────────────────────────────────────────────────────────
    p_start = subs.add_parser("start", help="Start Curie daemon in background")
    p_start.add_argument("--api", action="store_true", help="Enable API connector (default if none specified)")
    p_start.add_argument("--telegram", action="store_true", help="Enable Telegram connector")
    p_start.add_argument("--discord", action="store_true", help="Enable Discord connector")
    p_start.add_argument("--all", dest="all_connectors", action="store_true", help="Enable all connectors")
    p_start.set_defaults(func=_cmd_start)

    # ── stop ───────────────────────────────────────────────────────────────
    p_stop = subs.add_parser("stop", help="Stop the running daemon")
    p_stop.set_defaults(func=_cmd_stop)

    # ── restart ────────────────────────────────────────────────────────────
    p_restart = subs.add_parser("restart", help="Restart the daemon")
    p_restart.add_argument("--api", action="store_true")
    p_restart.add_argument("--telegram", action="store_true")
    p_restart.add_argument("--discord", action="store_true")
    p_restart.add_argument("--all", dest="all_connectors", action="store_true")
    p_restart.set_defaults(func=_cmd_restart)

    # ── status ─────────────────────────────────────────────────────────────
    p_status = subs.add_parser("status", help="Show daemon / agent status")
    p_status.set_defaults(func=_cmd_status)

    # ── metrics ────────────────────────────────────────────────────────────
    p_metrics = subs.add_parser("metrics", help="Live system metrics dashboard (CPU, RAM, GPU, …)")
    p_metrics.add_argument("--once", action="store_true", help="Show a one-shot snapshot and exit")
    p_metrics.add_argument("--interval", type=float, default=1.0, metavar="SECS",
                           help="Refresh interval in seconds (default: 1.0)")
    p_metrics.set_defaults(func=_cmd_metrics)

    # ── tasks ──────────────────────────────────────────────────────────────
    p_tasks = subs.add_parser("tasks", help="Show task and sub-agent breakdown")
    p_tasks.add_argument("--live", action="store_true", help="Live-updating view (refresh every second)")
    p_tasks.add_argument("--all", dest="all", action="store_true", help="Include finished tasks")
    p_tasks.add_argument(
        "--tree",
        action="store_true",
        help="Show a tree visualization of tasks and sub-agents (combine with --live for animated view)",
    )
    p_tasks.add_argument(
        "--visual",
        action="store_true",
        help="Show animated ASCII character visualization (combine with --live for animation)",
    )
    p_tasks.add_argument(
        "--web",
        action="store_true",
        help="Open a browser-based animated Curie dashboard with live sub-agent cards",
    )
    p_tasks.add_argument(
        "--canvas",
        action="store_true",
        help="Open the Live Canvas node-graph workspace in the browser",
    )
    p_tasks.set_defaults(func=_cmd_tasks)

    # ── agent ──────────────────────────────────────────────────────────────
    p_agent = subs.add_parser("agent", help="Chat with Curie (interactive or single message)")
    p_agent.add_argument("-m", "--message", type=str, default=None, metavar="MSG",
                         help="Send a single message and exit")
    p_agent.add_argument("--no-api", action="store_true",
                         help="Force in-process mode (do not try the HTTP API)")
    p_agent.add_argument("--api-url", type=str, default=None, metavar="URL",
                         help="Base URL of the running Curie API (default: http://127.0.0.1:8000)")
    p_agent.set_defaults(func=_cmd_agent)

    # ── doctor ─────────────────────────────────────────────────────────────
    p_doctor = subs.add_parser("doctor", help="Run system diagnostics")
    p_doctor.add_argument("--verbose", action="store_true", help="Show extra detail")
    p_doctor.set_defaults(func=_cmd_doctor)

    # ── service ────────────────────────────────────────────────────────────
    p_service = subs.add_parser("service", help="Manage Curie as an OS service")
    p_service.add_argument(
        "service_action",
        choices=["install", "start", "stop", "restart", "status"],
        metavar="ACTION",
        help="install | start | stop | restart | status",
    )
    p_service.set_defaults(func=_cmd_service)

    # ── logs ───────────────────────────────────────────────────────────────
    p_logs = subs.add_parser("logs", help="Show / follow daemon log output")
    p_logs.add_argument("-n", "--lines", type=int, default=50, metavar="N",
                        help="Number of lines to show (default: 50)")
    p_logs.add_argument("-f", "--follow", action="store_true",
                        help="Follow log output (like tail -f)")
    p_logs.set_defaults(func=_cmd_logs)

    # ── onboard ────────────────────────────────────────────────────────────
    p_onboard = subs.add_parser("onboard", help="Guided first-time setup wizard")
    p_onboard.add_argument("--verbose", action="store_true", help="Show extra detail after setup")
    p_onboard.set_defaults(func=_cmd_onboard)

    # ── channel ────────────────────────────────────────────────────────────
    p_channel = subs.add_parser("channel", help="Manage chat channel connectors")
    p_channel.add_argument(
        "channel_action",
        choices=["list", "doctor", "bind-telegram", "bind-discord"],
        metavar="ACTION",
        help="list | doctor | bind-telegram TOKEN | bind-discord TOKEN",
    )
    p_channel.add_argument("token", nargs="?", default=None, metavar="TOKEN",
                           help="Bot token (for bind-* actions)")
    p_channel.set_defaults(func=_cmd_channel)

    # ── cron ───────────────────────────────────────────────────────────────
    p_cron = subs.add_parser("cron", help="Manage scheduled prompt jobs")
    cron_subs = p_cron.add_subparsers(dest="cron_action", metavar="ACTION")

    cron_subs.add_parser("list", help="List all scheduled jobs")

    p_cron_add = cron_subs.add_parser("add", help="Add a new scheduled job")
    p_cron_add.add_argument("schedule", metavar="SCHEDULE",
                            help="Cron expression, e.g. '*/5 * * * *' or '@hourly'")
    p_cron_add.add_argument("--prompt", required=True, metavar="TEXT",
                            help="Prompt to run on schedule")

    p_cron_remove = cron_subs.add_parser("remove", help="Remove a job by ID")
    p_cron_remove.add_argument("job_id", metavar="ID")

    p_cron_enable = cron_subs.add_parser("enable", help="Enable a disabled job")
    p_cron_enable.add_argument("job_id", metavar="ID")

    p_cron_disable = cron_subs.add_parser("disable", help="Disable a job without removing it")
    p_cron_disable.add_argument("job_id", metavar="ID")

    p_cron.set_defaults(func=_cmd_cron)

    # ── memory ─────────────────────────────────────────────────────────────
    p_mem = subs.add_parser("memory", help="Inspect and manage user memory")
    mem_subs = p_mem.add_subparsers(dest="memory_action", metavar="ACTION")

    p_mem_list = mem_subs.add_parser("list", help="List users and fact counts (with key preview)")
    p_mem_list.add_argument("--limit", type=int, default=20, metavar="N",
                            help="Max number of users to show (default: 20)")

    p_mem_keys = mem_subs.add_parser(
        "keys", help="List all memory key names and value previews for a user"
    )
    p_mem_keys.add_argument("--user", metavar="INTERNAL_ID", default=None,
                            help="Target user (default: MASTER_USER_ID)")

    p_mem_get = mem_subs.add_parser("get", help="Get the full value of a memory key")
    p_mem_get.add_argument("key", metavar="KEY")
    p_mem_get.add_argument("--user", metavar="INTERNAL_ID", default=None,
                           help="Target user (default: MASTER_USER_ID)")

    mem_subs.add_parser("stats", help="Aggregate memory statistics")

    p_mem_clear = mem_subs.add_parser("clear-user", help="Clear session memory for a user")
    p_mem_clear.add_argument("user_id", metavar="INTERNAL_ID")

    p_mem.set_defaults(func=_cmd_memory)

    # ── auth ───────────────────────────────────────────────────────────────
    p_auth = subs.add_parser("auth", help="Manage LLM provider credentials")
    auth_subs = p_auth.add_subparsers(dest="auth_action", metavar="ACTION")

    p_auth_login = auth_subs.add_parser("login", help="Store an API key for a provider")
    p_auth_login.add_argument(
        "--provider", required=False, default=None,
        choices=["openai", "anthropic", "gemini", "llama.cpp"],
        help="Provider name (interactive selector if omitted)",
    )
    p_auth_login.add_argument("--key", default=None, metavar="API_KEY",
                              help="API key (omit to enter interactively)")

    auth_subs.add_parser("status", help="Show configured LLM providers")

    p_auth_use = auth_subs.add_parser("use", help="Set active LLM provider priority")
    p_auth_use.add_argument(
        "--provider", required=False, default=None,
        choices=["openai", "anthropic", "gemini", "llama.cpp"],
        help="Provider to move to the top of the priority list (interactive selector if omitted)",
    )

    p_auth.set_defaults(func=_cmd_auth)

    # ── completions ────────────────────────────────────────────────────────
    p_completions = subs.add_parser("completions", help="Generate shell completion scripts")
    p_completions.add_argument(
        "shell",
        choices=["bash", "zsh", "fish"],
        metavar="SHELL",
        help="Target shell: bash | zsh | fish",
    )
    p_completions.set_defaults(func=_cmd_completions)

    # ── hardware ───────────────────────────────────────────────────────────
    p_hw = subs.add_parser(
        "hardware",
        help="Hardware device management",
        description="Scan for and inspect connected hardware devices.",
    )
    hw_subs = p_hw.add_subparsers(dest="hardware_action", metavar="ACTION")
    hw_subs.add_parser(
        "discover",
        help="Scan for connected devices: USB, serial, audio, cameras, Bluetooth, network",
    )
    p_hw.set_defaults(func=_cmd_hardware)

    # ── peripheral ─────────────────────────────────────────────────────────
    p_periph = subs.add_parser(
        "peripheral",
        help="List connected peripherals (from cache or fresh scan)",
        description=(
            "Show connected peripherals.  Uses the cached result from\n"
            "~/.curie/peripherals.json.  Pass --fresh to re-scan."
        ),
    )
    p_periph_subs = p_periph.add_subparsers(dest="peripheral_action", metavar="ACTION")
    p_periph_list = p_periph_subs.add_parser(
        "list",
        help="List peripherals (from cache; use --fresh to re-scan)",
    )
    p_periph_list.add_argument(
        "--fresh", action="store_true",
        help="Re-scan devices instead of using the cache",
    )
    p_periph.set_defaults(func=_cmd_peripheral)

    # ── help ───────────────────────────────────────────────────────────────
    p_help = subs.add_parser(
        "help",
        help="Show full command reference with descriptions and examples",
    )
    p_help.set_defaults(func=_cmd_help)

    # ── canvas ─────────────────────────────────────────────────────────────
    p_canvas = subs.add_parser(
        "canvas",
        help="Open the Live Canvas node-graph workspace in the browser",
        description=(
            "Live Canvas — agent-driven visual workspace.\n\n"
            "Shows all running tasks as interactive nodes with animated edges.\n"
            "Drag nodes, scroll to zoom, hover for details."
        ),
    )
    p_canvas.add_argument(
        "--all",
        dest="all",
        action="store_true",
        help="Include finished tasks",
    )
    p_canvas.set_defaults(func=_cmd_canvas)

    # ── sessions ───────────────────────────────────────────────────────────
    p_sessions = subs.add_parser(
        "sessions",
        help="Inspect and manage conversation sessions",
    )
    sess_subs = p_sessions.add_subparsers(dest="sessions_action", metavar="ACTION")

    p_sess_list = sess_subs.add_parser("list", help="List active sessions")
    p_sess_list.add_argument(
        "--channel",
        default=None,
        metavar="CHANNEL",
        help="Filter by channel (telegram, discord, slack, api, …)",
    )

    p_sess_clear = sess_subs.add_parser("clear", help="Clear session memory for a user")
    p_sess_clear.add_argument("--user-id", dest="user_id", required=True, metavar="ID")
    p_sess_clear.add_argument(
        "--channel",
        default=None,
        metavar="CHANNEL",
        help="Clear only the session for this channel (default: all channels)",
    )

    sess_subs.add_parser("stats", help="Session statistics (counts, message totals)")

    p_sessions.set_defaults(func=_cmd_sessions)

    # ── tools ──────────────────────────────────────────────────────────────
    p_tools = subs.add_parser(
        "tools",
        help="List and inspect available first-class tools / skills",
    )
    p_tools.add_argument(
        "--category",
        default=None,
        choices=["skill", "connector", "service", "canvas"],
        metavar="CATEGORY",
        help="Filter by category: skill | connector | service | canvas",
    )
    p_tools.add_argument(
        "--tag",
        default=None,
        metavar="TAG",
        help="Filter by tag (e.g. web, coding, messaging)",
    )
    p_tools.add_argument(
        "--available",
        dest="available_only",
        action="store_true",
        help="Show only tools whose dependencies are satisfied",
    )
    p_tools.set_defaults(func=_cmd_tools)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        # Show the beautiful full help instead of bare argparse usage
        try:
            from cli.help_cmd import print_full_help
            return print_full_help()
        except Exception:
            parser.print_help()
        return 0

    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    sys.exit(main())
