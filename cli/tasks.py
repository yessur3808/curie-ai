# cli/tasks.py
"""
Task and sub-agent tracking registry.

Other modules write task/sub-agent updates to ~/.curie/tasks.json.
The CLI reads and renders a live breakdown table.
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any

CURIE_DIR = Path.home() / ".curie"
TASKS_FILE = CURIE_DIR / "tasks.json"

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _load_raw() -> dict:
    if not TASKS_FILE.exists():
        return {"tasks": {}}
    try:
        return json.loads(TASKS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"tasks": {}}


def _save_raw(data: dict) -> None:
    CURIE_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Public write API  (called by ChatWorkflow / Agent)
# ---------------------------------------------------------------------------


def register_task(task_id: str, description: str, channel: str = "unknown") -> None:
    """Register a new top-level task."""
    with _lock:
        data = _load_raw()
        data["tasks"][task_id] = {
            "id": task_id,
            "description": description,
            "channel": channel,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "sub_agents": {},
        }
        _save_raw(data)


def register_sub_agent(
    task_id: str,
    agent_id: str,
    role: str,
    model: str = "",
    description: str = "",
) -> None:
    """Register a sub-agent under a task."""
    with _lock:
        data = _load_raw()
        task = data["tasks"].get(task_id)
        if task is None:
            return
        task["sub_agents"][agent_id] = {
            "id": agent_id,
            "role": role,
            "model": model,
            "description": description,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result_summary": "",
        }
        _save_raw(data)


def update_sub_agent_description(task_id: str, agent_id: str, description: str) -> None:
    """Update the live description of what a sub-agent is currently doing."""
    with _lock:
        data = _load_raw()
        task = data["tasks"].get(task_id)
        if task is None:
            return
        agent = task["sub_agents"].get(agent_id)
        if agent is None:
            return
        agent["description"] = description
        _save_raw(data)


def update_sub_agent(task_id: str, agent_id: str, status: str, result_summary: str = "") -> None:
    """Mark a sub-agent as finished/failed."""
    with _lock:
        data = _load_raw()
        task = data["tasks"].get(task_id)
        if task is None:
            return
        agent = task["sub_agents"].get(agent_id)
        if agent is None:
            return
        agent["status"] = status
        agent["finished_at"] = time.time()
        agent["result_summary"] = result_summary
        _save_raw(data)


def finish_task(task_id: str, status: str = "done") -> None:
    """Mark a top-level task as finished."""
    with _lock:
        data = _load_raw()
        task = data["tasks"].get(task_id)
        if task is None:
            return
        task["status"] = status
        task["finished_at"] = time.time()
        _save_raw(data)


def clear_finished_tasks() -> int:
    """Remove all finished/failed tasks. Returns number removed."""
    with _lock:
        data = _load_raw()
        before = len(data["tasks"])
        data["tasks"] = {
            k: v for k, v in data["tasks"].items()
            if v.get("status") == "running"
        }
        _save_raw(data)
        return before - len(data["tasks"])


# ---------------------------------------------------------------------------
# Public read API  (called by CLI display)
# ---------------------------------------------------------------------------


def get_tasks() -> list[dict[str, Any]]:
    """Return a list of all tasks (newest first)."""
    data = _load_raw()
    tasks = list(data["tasks"].values())
    tasks.sort(key=lambda t: t.get("started_at", 0), reverse=True)
    return tasks


def get_running_tasks() -> list[dict[str, Any]]:
    return [t for t in get_tasks() if t.get("status") == "running"]


def get_task_summary() -> dict[str, int]:
    tasks = get_tasks()
    total_agents = sum(len(t.get("sub_agents", {})) for t in tasks)
    running_agents = sum(
        sum(1 for a in t.get("sub_agents", {}).values() if a.get("status") == "running")
        for t in tasks
    )
    return {
        "total_tasks": len(tasks),
        "running_tasks": len(get_running_tasks()),
        "total_sub_agents": total_agents,
        "running_sub_agents": running_agents,
    }
