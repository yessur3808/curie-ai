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
import re
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CURIE_DIR = Path.home() / ".curie"
PID_FILE = CURIE_DIR / "curie.pid"
LOG_FILE = CURIE_DIR / "curie.log"
STATE_FILE = CURIE_DIR / "daemon_state.json"


def _instance_name(instance: str | None = None) -> str:
    name = (instance or os.getenv("CURIE_INSTANCE") or "default").strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
        raise ValueError("Instance names may contain only letters, numbers, _ and -")
    return name


def _instance_paths(instance: str | None = None) -> tuple[Path, Path, Path]:
    name = _instance_name(instance)
    if name == "default":
        return PID_FILE, LOG_FILE, STATE_FILE
    return (
        CURIE_DIR / f"instance-{name}.pid",
        CURIE_DIR / f"instance-{name}.log",
        CURIE_DIR / f"instance-{name}-daemon-state.json",
    )


def _ensure_curie_dir() -> None:
    CURIE_DIR.mkdir(parents=True, exist_ok=True)


def _read_pid(instance: str | None = None) -> Optional[int]:
    pid_file, _, _ = _instance_paths(instance)
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_pid(pid: int, instance: str | None = None) -> None:
    _ensure_curie_dir()
    _instance_paths(instance)[0].write_text(str(pid))


def _remove_pid(instance: str | None = None) -> None:
    try:
        _instance_paths(instance)[0].unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Status checks should remain read-only and useful even when the Curie
        # state directory is mounted read-only or owned by another service
        # account. A stale PID is harmless; crashing the CLI is not.
        logger.warning("Could not remove stale PID file for %s: %s", instance, exc)


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _write_state(state: dict, instance: str | None = None) -> None:
    _ensure_curie_dir()
    _instance_paths(instance)[2].write_text(json.dumps(state, indent=2))


def read_daemon_state(instance: str | None = None) -> dict:
    """Return the last-written daemon state (or an empty dict)."""
    state_file = _instance_paths(instance)[2]
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_status(instance: str | None = None) -> dict:
    """Return a status dict: {running, pid, uptime_seconds, log_file}."""
    name = _instance_name(instance)
    _, log_file, _ = _instance_paths(name)
    pid = _read_pid(name)
    if pid is None:
        return {"running": False, "pid": None, "uptime_seconds": None, "log_file": str(log_file), "instance": name}

    if _is_process_running(pid):
        state = read_daemon_state(name)
        started_at = state.get("started_at")
        uptime = None
        if started_at:
            uptime = int(time.time() - started_at)
        return {
            "running": True,
            "pid": pid,
            "uptime_seconds": uptime,
            "log_file": str(log_file),
            "instance": name,
        }
    else:
        # Stale PID file
        _remove_pid(name)
        return {"running": False, "pid": None, "uptime_seconds": None, "log_file": str(log_file), "instance": name}


def start_daemon(extra_args: list[str] | None = None, connector_args: list[str] | None = None, instance: str | None = None) -> dict:
    """
    Start Curie as a background daemon.

    Returns a dict with {success, pid, message}.
    """
    name = _instance_name(instance)
    status = get_status(name)
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

    child_env = os.environ.copy()
    previous_state = read_daemon_state(name)
    startup_variant = int(previous_state.get("startup_variant", -1)) + 1
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(repo_root / ".env").items():
            if value is not None:
                child_env.setdefault(key, value)
        overlay = repo_root / "instances" / f"{name}.env"
        if name != "default":
            if not overlay.is_file():
                return {"success": False, "pid": None, "message": f"Instance environment not found: {overlay}"}
            for key, value in dotenv_values(overlay).items():
                if value is not None:
                    child_env[key] = value
        child_env["CURIE_INSTANCE"] = name
        child_env["CURIE_STARTUP_VARIANT"] = str(startup_variant)
    except Exception as exc:
        return {"success": False, "pid": None, "message": f"Could not load instance environment: {exc}"}

    # Add connector flags; default to --api if none provided
    if connector_args:
        cmd.extend(connector_args)
    elif extra_args:
        cmd.extend(extra_args)
    else:
        cmd.append("--api")

    _, log_file, _ = _instance_paths(name)
    with open(log_file, "a") as log_fd:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=child_env,
        )

    # Give the process a moment to fail fast
    time.sleep(0.5)
    if proc.poll() is not None:
        return {"success": False, "pid": None, "message": f"Process exited immediately. Check {log_file}"}

    _write_pid(proc.pid, name)
    _write_state({
        "started_at": time.time(), "pid": proc.pid, "cmd": cmd,
        "instance": name, "startup_variant": startup_variant,
    }, name)

    return {"success": True, "pid": proc.pid, "message": f"Instance {name} started in background (PID {proc.pid})"}


def stop_daemon(timeout: int = 10, instance: str | None = None) -> dict:
    """Stop the running daemon gracefully, then forcefully if needed."""
    name = _instance_name(instance)
    status = get_status(name)
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

    _remove_pid(name)
    return {"success": True, "message": f"Instance {name} stopped (PID {pid})"}


def restart_daemon(extra_args: list[str] | None = None, connector_args: list[str] | None = None, instance: str | None = None) -> dict:
    """Stop then start the daemon."""
    stop_result = stop_daemon(instance=instance)
    time.sleep(1)
    start_result = start_daemon(extra_args=extra_args, connector_args=connector_args, instance=instance)
    return {
        "stop": stop_result,
        "start": start_result,
        "success": start_result["success"],
        "message": start_result["message"],
    }
