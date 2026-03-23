# cli/service.py
"""
OS-level service management for Curie AI.
Supports systemd (Linux) and launchd (macOS).
"""

from __future__ import annotations

import os
import sys
import subprocess
import platform
from pathlib import Path
from typing import Literal

try:
    from rich.console import Console
    _console = Console()
    def _print(msg: str) -> None:
        _console.print(msg)
except ImportError:
    def _print(msg: str) -> None:
        # Strip basic rich tags
        import re
        print(re.sub(r"\[/?[a-zA-Z_ ]+\]", "", msg))

SYSTEM = platform.system()


# ─── systemd (Linux) ─────────────────────────────────────────────────────────

_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=Curie AI – Clever Understanding and Reasoning Intelligent Entity
After=network.target

[Service]
Type=simple
ExecStart={python} {main_py} --api
WorkingDirectory={work_dir}
EnvironmentFile={env_file}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

_LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.curie.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{main_py}</string>
        <string>--api</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{work_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{log_file}</string>
    <key>StandardErrorPath</key>
    <string>{log_file}</string>
</dict>
</plist>
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _service_name() -> str:
    return os.getenv("SYSTEMD_SERVICE_NAME", "curie-ai")


def _systemd_unit_path() -> Path:
    name = _service_name()
    return Path(f"/etc/systemd/system/{name}.service")


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "ai.curie.daemon.plist"


def _log_file() -> str:
    return str(Path.home() / ".curie" / "curie.log")


# ─── public interface ─────────────────────────────────────────────────────────


def install_service() -> int:
    """Install Curie as an OS service. Returns exit code."""
    root = _repo_root()
    python = sys.executable
    main_py = str(root / "main.py")
    work_dir = str(root)
    env_file = str(root / ".env")
    log_file = _log_file()

    if SYSTEM == "Linux":
        unit_content = _SYSTEMD_UNIT_TEMPLATE.format(
            python=python, main_py=main_py, work_dir=work_dir, env_file=env_file
        )
        unit_path = _systemd_unit_path()
        try:
            unit_path.write_text(unit_content)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", _service_name()], check=True)
            _print(f"[green]✅ systemd service installed:[/green] {unit_path}")
            _print(f"   Start with: [bold]curie service start[/bold]")
            return 0
        except PermissionError:
            _print("[red]❌ Need root to install systemd service. Try:[/red] sudo curie service install")
            return 1
        except subprocess.CalledProcessError as e:
            _print(f"[red]❌ systemctl error:[/red] {e}")
            return 1

    elif SYSTEM == "Darwin":
        plist_content = _LAUNCHD_PLIST_TEMPLATE.format(
            python=python, main_py=main_py, work_dir=work_dir, log_file=log_file
        )
        plist_path = _launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        _print(f"[green]✅ launchd plist installed:[/green] {plist_path}")
        _print(f"   Start with: [bold]curie service start[/bold]")
        return 0

    else:
        _print(f"[yellow]⚠️  OS service install not supported on {SYSTEM}.[/yellow]")
        _print("Use [bold]curie start[/bold] to run in background instead.")
        return 1


def _systemctl_action(action: str) -> int:
    try:
        subprocess.run(["systemctl", action, _service_name()], check=True)
        _print(f"[green]✅ systemd service {action}ed[/green]")
        return 0
    except subprocess.CalledProcessError as e:
        _print(f"[red]❌ systemctl {action} failed:[/red] {e}")
        return 1


def _launchctl_action(action: str) -> int:
    plist = str(_launchd_plist_path())
    try:
        if action in ("start", "load"):
            subprocess.run(["launchctl", "load", plist], check=True)
        elif action in ("stop", "unload"):
            subprocess.run(["launchctl", "unload", plist], check=True)
        elif action == "restart":
            subprocess.run(["launchctl", "unload", plist])
            subprocess.run(["launchctl", "load", plist], check=True)
        _print(f"[green]✅ launchd service {action}[/green]")
        return 0
    except subprocess.CalledProcessError as e:
        _print(f"[red]❌ launchctl {action} failed:[/red] {e}")
        return 1


def service_action(action: Literal["start", "stop", "restart", "status"]) -> int:
    """Perform a service action (start/stop/restart/status). Returns exit code."""
    if action == "status":
        return service_status()

    if SYSTEM == "Linux":
        return _systemctl_action(action)
    elif SYSTEM == "Darwin":
        return _launchctl_action(action)
    else:
        _print(f"[yellow]⚠️  Service {action} not supported on {SYSTEM}.[/yellow]")
        return 1


def service_status() -> int:
    """Print service status. Returns 0 if running."""
    if SYSTEM == "Linux":
        result = subprocess.run(
            ["systemctl", "is-active", _service_name()],
            capture_output=True, text=True
        )
        active = result.stdout.strip() == "active"
        _print(f"Systemd service [bold]{_service_name()}[/bold]: "
               f"[green]{result.stdout.strip()}[/green]" if active
               else f"[yellow]{result.stdout.strip()}[/yellow]")
        return 0 if active else 1

    elif SYSTEM == "Darwin":
        plist = _launchd_plist_path()
        _print(f"launchd plist: {'[green]exists[/green]' if plist.exists() else '[red]not installed[/red]'}")
        return 0 if plist.exists() else 1

    else:
        # Fall back to daemon PID check
        from cli.daemon import get_status
        st = get_status()
        if st["running"]:
            _print(f"[green]Curie daemon running[/green] (PID {st['pid']})")
            return 0
        _print("[yellow]Curie daemon not running[/yellow]")
        return 1
