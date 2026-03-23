# cli/agent_cmd.py
"""
CLI interface to the Curie agent: interactive chat and single-message mode.
Works standalone (without a running daemon) or by calling the HTTP API if the
daemon is running on localhost:8000.
"""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


def _print_response(text: str, label: str = "Curie") -> None:
    if RICH_AVAILABLE and console:
        console.print(Panel(Markdown(text), title=f"[cyan]{label}[/cyan]", box=box.ROUNDED))
    else:
        print(f"\n{label}:\n{text}\n")


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
        # Lazy imports so the CLI doesn't require the full stack just for --help
        repo_root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(repo_root))

        # Minimal env bootstrap
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


def run_single_message(message: str, use_api: bool = True, api_url: str = "http://127.0.0.1:8000") -> None:
    """Send a single message to Curie and print the response."""
    if RICH_AVAILABLE and console:
        console.print(f"[bold]You:[/bold] {message}")

    if use_api:
        response = _via_api(message, api_url)
        if response is None:
            # daemon not running – fall back to in-process
            response = _via_workflow(message)
    else:
        response = _via_workflow(message)

    _print_response(response)


def run_interactive_chat(use_api: bool = True, api_url: str = "http://127.0.0.1:8000") -> None:
    """Start an interactive REPL-style chat session with Curie."""
    if RICH_AVAILABLE and console:
        console.print(Panel(
            "[bold cyan]Curie AI – Interactive Chat[/bold cyan]\n"
            "Type [bold]exit[/bold] or [bold]quit[/bold] to leave. "
            "[bold]Ctrl-C[/bold] also works.",
            box=box.ROUNDED,
        ))
    else:
        print("Curie AI – Interactive Chat (type 'exit' to quit)\n")

    # Try to determine if daemon is available
    _api_available = use_api and (_via_api("ping", api_url) is not None)

    # In-process workflow (created once for the session)
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
            import asyncio
            persona = load_persona()
            workflow = ChatWorkflow(persona=persona, max_history=5, enable_small_talk=False)
            _loop = asyncio.new_event_loop()
        except Exception as e:
            print(f"Failed to initialise workflow: {e}")
            return

    import asyncio

    while True:
        try:
            if RICH_AVAILABLE and console:
                console.print("[bold green]You >[/bold green] ", end="")
                user_input = input()
            else:
                user_input = input("You > ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user_input.strip().lower() in ("exit", "quit", "bye", ":q"):
            break
        if not user_input.strip():
            continue

        if _api_available:
            response = _via_api(user_input, api_url)
            if response is None:
                response = "[no response from daemon]"
        else:
            try:
                loop = asyncio.new_event_loop()
                response = loop.run_until_complete(
                    workflow.process_message(
                        user_message=user_input,
                        platform="cli",
                        external_user_id="cli_user",
                    )
                )
                loop.close()
                if not isinstance(response, str):
                    response = str(response)
            except Exception as e:
                response = f"[error: {e}]"

        _print_response(response)
