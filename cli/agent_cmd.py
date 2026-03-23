# cli/agent_cmd.py
"""
CLI interface to the Curie agent: interactive chat and single-message mode.
Works standalone (without a running daemon) or by calling the HTTP API if the
daemon is running on localhost:8000.

Features
--------
  - Rich-formatted responses in a panel with sender label
  - Per-message timestamps
  - Animated "Curie is thinking…" spinner while waiting for a reply
  - Graceful fallback when Rich is not installed
"""

from __future__ import annotations

import os
import sys
import json
from datetime import datetime
from pathlib import Path

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.rule import Rule
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _print_response(text: str, label: str = "Curie") -> None:
    ts = _timestamp()
    if RICH_AVAILABLE and console:
        console.print(
            Panel(
                Markdown(text),
                title=f"[cyan]{label}[/cyan]",
                subtitle=f"[dim]{ts}[/dim]",
                box=box.ROUNDED,
            )
        )
    else:
        print(f"\n[{ts}] {label}:\n{text}\n")


def _print_user_line(message: str) -> None:
    ts = _timestamp()
    if RICH_AVAILABLE and console:
        console.print(f"[bold green]You[/bold green]  [dim]{ts}[/dim]  {message}")
    else:
        print(f"[{ts}] You: {message}")


def _via_api(message: str, base_url: str = "http://127.0.0.1:8000") -> str | None:
    """Try to send a message through the running HTTP API. Returns None on error."""
    try:
        import requests
        resp = requests.post(
            f"{base_url}/chat",
            json={"message": message, "user_id": "cli_user"},
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("response") or data.get("message") or str(data)
    except Exception:
        pass
    return None


def _via_workflow(message: str) -> str:
    """
    Invoke the ChatWorkflow directly in-process (no daemon needed).
    Falls back to a plain string on error.
    """
    try:
        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root))

        env_file = repo_root / ".env"
        if env_file.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file)
            except ImportError:
                pass

        from utils.persona import load_persona
        from agent.chat_workflow import ChatWorkflow

        persona = load_persona()
        workflow = ChatWorkflow(persona=persona, max_history=5, enable_small_talk=False)
        import asyncio
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            workflow.process_message(
                user_message=message,
                platform="cli",
                external_user_id="cli_user",
            )
        )
        loop.close()
        return result if isinstance(result, str) else str(result)
    except Exception as e:
        return f"[error invoking workflow: {e}]"


def _fetch_with_spinner(
    message: str,
    *,
    use_api: bool,
    api_url: str,
    api_available: bool,
    workflow: object | None,
) -> str:
    """Fetch a response, showing a Rich spinner while waiting."""
    from cli.ui import spinner as _spinner  # noqa: PLC0415

    response: str = ""

    with _spinner("Curie is thinking…"):
        if use_api and api_available:
            resp = _via_api(message, api_url)
            if resp is not None:
                response = resp
            else:
                response = "[no response from daemon]"
        else:
            if workflow is not None:
                try:
                    import asyncio
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(
                        workflow.process_message(  # type: ignore[union-attr]
                            user_message=message,
                            platform="cli",
                            external_user_id="cli_user",
                        )
                    )
                    loop.close()
                    response = result if isinstance(result, str) else str(result)
                except Exception as e:
                    response = f"[error: {e}]"
            else:
                response = _via_workflow(message)

    return response


def run_single_message(message: str, use_api: bool = True, api_url: str = "http://127.0.0.1:8000") -> None:
    """Send a single message to Curie and print the response."""
    _print_user_line(message)
    response = _fetch_with_spinner(
        message,
        use_api=use_api,
        api_url=api_url,
        api_available=use_api,
        workflow=None,
    )
    _print_response(response)


def run_interactive_chat(use_api: bool = True, api_url: str = "http://127.0.0.1:8000") -> None:
    """Start an interactive REPL-style chat session with Curie."""
    if RICH_AVAILABLE and console:
        console.print(
            Panel(
                "[bold cyan]Curie AI – Interactive Chat[/bold cyan]\n\n"
                "Type [bold]exit[/bold] or [bold]quit[/bold] to leave.  "
                "[bold]Ctrl-C[/bold] also works.\n"
                "[dim]Responses are rendered as Markdown.[/dim]",
                box=box.ROUNDED,
                padding=(1, 4),
            )
        )
    else:
        print("Curie AI – Interactive Chat (type 'exit' to quit)\n")

    # Check if the API daemon is reachable
    _api_available = use_api and (_via_api("ping", api_url) is not None)

    workflow = None
    if not _api_available:
        if RICH_AVAILABLE and console:
            console.print("[yellow]⚠️  Daemon not reachable – running in-process.[/yellow]\n")
        try:
            repo_root = Path(__file__).resolve().parent.parent
            sys.path.insert(0, str(repo_root))
            env_file = repo_root / ".env"
            if env_file.exists():
                try:
                    from dotenv import load_dotenv
                    load_dotenv(env_file)
                except ImportError:
                    pass
            from utils.persona import load_persona
            from agent.chat_workflow import ChatWorkflow
            persona = load_persona()
            workflow = ChatWorkflow(persona=persona, max_history=5, enable_small_talk=False)
        except Exception as e:
            print(f"Failed to initialize workflow: {e}")
            return

    while True:
        try:
            if RICH_AVAILABLE and console:
                console.print("[bold green]You ›[/bold green] ", end="")
            user_input = input()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user_input.strip().lower() in ("exit", "quit", "bye", ":q"):
            if RICH_AVAILABLE and console:
                console.print("[dim]Goodbye 👋[/dim]")
            break
        if not user_input.strip():
            continue

        response = _fetch_with_spinner(
            user_input,
            use_api=use_api,
            api_url=api_url,
            api_available=_api_available,
            workflow=workflow,
        )
        _print_response(response)
