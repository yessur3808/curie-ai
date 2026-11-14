"""Read-only view of the independent recovery watchdog; no model shell access."""

import json
import time
from pathlib import Path

PATHS = (
    Path("/var/lib/curie-recovery/status.json"),
    Path("/home/curlycoffee3808/.local/state/curie-recovery/status.json"),
)


def recovery_status():
    for path in PATHS:
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                continue
            age = max(0, time.time() - float(data.get("checked_at", 0)))
            if age > 600:
                continue
            return {
                "ready": True,
                "status": data.get("status", "unknown"),
                "age_seconds": round(age),
                "processes": data.get("processes", {}),
                "host_reboot_enabled": bool(data.get("host_reboot_enabled")),
                "gpu_render_available": bool(data.get("gpu_render_available")),
                "last_actions": data.get("last_actions", []),
            }
        except (OSError, ValueError, TypeError):
            continue
    return {"ready": False, "status": "not reporting", "host_reboot_enabled": False}


def handle_recovery_command(text):
    if text.strip().casefold() not in {
        "/selfheal",
        "/recovery",
        "show your recovery status",
    }:
        return None
    state = recovery_status()
    if not state["ready"]:
        return "My recovery watchdog isn't reporting yet. I can't claim automatic repairs are active."
    lines = [
        f"My recovery watchdog is {state['status']} ({state['age_seconds']}s ago)."
    ]
    lines.extend(f"{name}: {status}" for name, status in state["processes"].items())
    lines.append(
        "Automatic host reboot: "
        + (
            "enabled for persistent GPU-reset failures only."
            if state["host_reboot_enabled"]
            else "not enabled."
        )
    )
    lines.append(
        "I respect deliberate stops and limit restart attempts. This does not grant me arbitrary code changes or shell commands."
    )
    return "\n".join(lines)
