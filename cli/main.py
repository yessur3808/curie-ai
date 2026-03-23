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
  curie agent                 # Interactive chat with Curie
  curie agent -m "hello"      # Single message
  curie doctor                # System diagnostics
  curie service install       # Install as OS service
  curie service start|stop|restart|status
  curie logs [--lines N]      # Tail the daemon log
"""

from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path


# ─── sub-command handlers ─────────────────────────────────────────────────────


def _cmd_start(args: argparse.Namespace) -> int:
    from cli.daemon import start_daemon
    connector_args = []
    if args.telegram:
        connector_args.append("--telegram")
    if args.discord:
        connector_args.append("--discord")
    if args.api:
        connector_args.append("--api")
    if args.all_connectors:
        connector_args = ["--all"]
    result = start_daemon(connector_args=connector_args if connector_args else None)
    print(result["message"])
    return 0 if result["success"] else 1


def _cmd_stop(args: argparse.Namespace) -> int:
    from cli.daemon import stop_daemon
    result = stop_daemon()
    print(result["message"])
    return 0 if result["success"] else 1


def _cmd_restart(args: argparse.Namespace) -> int:
    from cli.daemon import restart_daemon
    connector_args = []
    if args.telegram:
        connector_args.append("--telegram")
    if args.discord:
        connector_args.append("--discord")
    if args.api:
        connector_args.append("--api")
    if args.all_connectors:
        connector_args = ["--all"]
    result = restart_daemon(connector_args=connector_args if connector_args else None)
    print(result["message"])
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
    from cli.tasks_display import show_tasks
    show_tasks(show_finished=args.all, live=args.live)
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
    n = args.lines
    if not LOG_FILE.exists():
        print(f"Log file not found: {LOG_FILE}")
        return 1
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
        for line in lines[-n:]:
            print(line)
    except OSError as e:
        print(f"Could not read log: {e}")
        return 1

    if args.follow:
        import time
        try:
            with open(LOG_FILE, errors="replace") as f:
                f.seek(0, 2)  # seek to end
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.2)
        except KeyboardInterrupt:
            pass
    return 0


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
  curie agent                      Interactive chat
  curie agent -m "hello"           Single message
  curie doctor                     System diagnostics
  curie service install            Install as OS service
  curie service start              Start OS service
  curie logs -n 50                 Show last 50 log lines
  curie logs -f                    Follow log output
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
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
