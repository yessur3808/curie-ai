# cli/daemon.py
"""
Daemon management: start/stop/restart/status for the Curie background process.
Uses a PID file in ~/.curie/ to track the running process.
"""

from __future__ import annotations

import os
import sys
import signal
import subprocess
import time
import json
from pathlib import Path
from typing import Optional

CURIE_DIR = Path.home() / ".curie"
PID_FILE = CURIE_DIR / "curie.pid"
LOG_FILE = CURIE_DIR / "curie.log"
STATE_FILE = CURIE_DIR / "daemon_state.json"


def _ensure_curie_dir() -> None:
    CURIE_DIR.mkdir(parents=True, exist_ok=True)


def _read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_pid(pid: int) -> None:
    _ensure_curie_dir()
    PID_FILE.write_text(str(pid))


def _remove_pid() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _write_state(state: dict) -> None:
    _ensure_curie_dir()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def read_daemon_state() -> dict:
    """Return the last-written daemon state (or an empty dict)."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_status() -> dict:
    """Return a status dict: {running, pid, uptime_seconds, log_file}."""
    pid = _read_pid()
    if pid is None:
        return {"running": False, "pid": None, "uptime_seconds": None, "log_file": str(LOG_FILE)}

    if _is_process_running(pid):
        state = read_daemon_state()
        started_at = state.get("started_at")
        uptime = None
        if started_at:
            uptime = int(time.time() - started_at)
        return {
            "running": True,
            "pid": pid,
            "uptime_seconds": uptime,
            "log_file": str(LOG_FILE),
        }
    else:
        # Stale PID file
        _remove_pid()
        return {"running": False, "pid": None, "uptime_seconds": None, "log_file": str(LOG_FILE)}


def start_daemon(extra_args: list[str] | None = None, connector_args: list[str] | None = None) -> dict:
    """
    Start Curie as a background daemon.

    Returns a dict with {success, pid, message}.
    """
    status = get_status()
    if status["running"]:
        return {"success": False, "pid": status["pid"], "message": f"Curie is already running (PID {status['pid']})"}

    _ensure_curie_dir()

    # Locate main.py relative to this file
    repo_root = Path(__file__).resolve().parent.parent
    main_script = repo_root / "main.py"
    if not main_script.exists():
        return {"success": False, "pid": None, "message": f"main.py not found at {main_script}"}

    # Build command: use the same Python interpreter that's running now
    python = sys.executable
    cmd = [python, str(main_script)]

    # Add connector flags; default to --api if none provided
    if connector_args:
        cmd.extend(connector_args)
    elif extra_args:
        cmd.extend(extra_args)
    else:
        cmd.append("--api")

    with open(LOG_FILE, "a") as log_fd:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Give the process a moment to fail fast
    time.sleep(0.5)
    if proc.poll() is not None:
        return {"success": False, "pid": None, "message": f"Process exited immediately. Check {LOG_FILE}"}

    _write_pid(proc.pid)
    _write_state({"started_at": time.time(), "pid": proc.pid, "cmd": cmd})

    return {"success": True, "pid": proc.pid, "message": f"Curie started in background (PID {proc.pid})"}


def stop_daemon(timeout: int = 10) -> dict:
    """Stop the running daemon gracefully, then forcefully if needed."""
    status = get_status()
    if not status["running"]:
        return {"success": False, "message": "Curie is not running"}

    pid = status["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(timeout * 10):
            if not _is_process_running(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    _remove_pid()
    return {"success": True, "message": f"Curie stopped (PID {pid})"}


def restart_daemon(extra_args: list[str] | None = None, connector_args: list[str] | None = None) -> dict:
    """Stop then start the daemon."""
    stop_result = stop_daemon()
    time.sleep(1)
    start_result = start_daemon(extra_args=extra_args, connector_args=connector_args)
    return {
        "stop": stop_result,
        "start": start_result,
        "success": start_result["success"],
        "message": start_result["message"],
    }
