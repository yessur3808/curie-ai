#!/usr/bin/env python3
"""Independent, bounded Curie recovery. No model decisions or arbitrary commands."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

OWNER = "curlycoffee3808"
ROOT = Path("/home/curlycoffee3808/Desktop/server/bots/curie-ai")
NODE = "/home/curlycoffee3808/.nvm/versions/node/v24.18.0/bin"
TARGETS = ("curie-inference", "curie-main")
PRIVILEGED_STATE = Path("/var/lib/curie-recovery")
USER_STATE = Path("/home/curlycoffee3808/.local/state/curie-recovery")


def run(args, timeout=15):
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "PATH": NODE + ":/usr/sbin:/usr/bin:/bin",
                "HOME": "/home/curlycoffee3808",
                "PM2_HOME": "/home/curlycoffee3808/.pm2",
            },
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def pm2(*args):
    command = [NODE + "/node", NODE + "/pm2", *args]
    if os.geteuid() == 0:
        command = ["/usr/sbin/runuser", "-u", OWNER, "--", *command]
    return run(command)


def choose_actions(
    state,
    statuses,
    now,
    *,
    gpu_fault=False,
    uptime=0,
    reboot_enabled=False,
    applying=False,
):
    """Pure policy. Missing, stopped, starting, and slow services are not crashed."""
    history = state.setdefault("restart_history", {})
    failures = state.setdefault("consecutive_failures", {})
    actions = []
    for name in TARGETS:
        status = statuses.get(name, "unknown")
        failures[name] = failures.get(name, 0) + 1 if status == "errored" else 0
        attempts = [stamp for stamp in history.get(name, []) if now - stamp < 86400]
        history[name] = attempts
        eligible = (
            failures[name] >= 3
            and len(attempts) < 3
            and (not attempts or now - attempts[-1] >= 900)
        )
        if applying and eligible:
            # Reserve the attempt before executing. A failed action still consumes budget.
            attempts.append(now)
            actions.append({"action": "restart", "target": name})
    last_reboot = state.get("last_reboot_at")
    inference_attempts = history.get("curie-inference", [])
    can_reboot = (
        applying
        and reboot_enabled
        and gpu_fault
        and uptime >= 1800
        and failures.get("curie-inference", 0) >= 3
        and len(inference_attempts) >= 2
        and now - inference_attempts[-1] >= 900
        and (last_reboot is None or now - last_reboot >= 86400)
        and not actions
    )
    if can_reboot:
        state["last_reboot_at"] = now
        actions.append({"action": "reboot", "target": "host"})
    return actions


def kernel_gpu_fault():
    code, output = run(
        ["/usr/bin/journalctl", "-k", "--since=-10min", "--no-pager", "-o", "cat"]
    )
    if code:
        return False
    return any(
        "amdgpu" in line.lower()
        and any(
            marker in line.lower()
            for marker in ("gpu reset failed", "gpu recovery failed")
        )
        for line in output.splitlines()
    )


def save(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.chmod(0o644)
    temporary.replace(path)


def tick(*, apply=False):
    state_dir = PRIVILEGED_STATE if os.geteuid() == 0 else USER_STATE
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    with (state_dir / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = state_dir / "status.json"
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(state, dict):
                raise ValueError("invalid state")
        except (OSError, ValueError):
            # Never reset the restart/reboot budget after corrupt state.
            return {"status": "blocked", "reason": "recovery state unreadable"}
        code, output = pm2("jlist")
        if code:
            state.update(
                status="degraded",
                reason="process manager unavailable",
                checked_at=time.time(),
            )
            save(path, state)
            return state
        try:
            processes = json.loads(output)
            statuses = {
                p["name"]: p.get("pm2_env", {}).get("status", "unknown")
                for p in processes
            }
        except (ValueError, KeyError, TypeError):
            return {"status": "blocked", "reason": "invalid process-manager status"}
        now = time.time()
        privileged = os.geteuid() == 0
        config_path = Path("/etc/curie-recovery.json")
        config = {}
        if privileged and config_path.exists():
            info = config_path.stat()
            if info.st_uid == 0 and not info.st_mode & 0o022:
                config = json.loads(config_path.read_text())
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        gpu_fault = kernel_gpu_fault() if privileged else False
        command_line = Path("/proc/cmdline").read_text()
        boot_test = any(
            flag in command_line for flag in ("yi_gpu_test=1", "curie_gpu_test=1")
        )
        actions = choose_actions(
            state,
            statuses,
            now,
            gpu_fault=gpu_fault,
            uptime=uptime,
            reboot_enabled=bool(config.get("host_reboot_enabled")) and not boot_test,
            applying=apply,
        )
        state.update(
            checked_at=now,
            processes={k: statuses.get(k, "missing") for k in TARGETS},
            gpu_fault=gpu_fault,
            gpu_render_available=bool(list(Path("/dev/dri").glob("renderD*"))),
            disk_free_bytes=shutil.disk_usage(ROOT).free,
            status=(
                "healthy"
                if all(statuses.get(k) == "online" for k in TARGETS)
                else "degraded"
            ),
            host_reboot_enabled=bool(config.get("host_reboot_enabled"))
            and privileged
            and not boot_test,
            last_actions=actions,
        )
        state["events"] = (
            state.get("events", []) + [{"at": now, **action} for action in actions]
        )[-100:]
        save(path, state)
        for action in actions:
            if action["action"] == "restart":
                result, _ = pm2("restart", action["target"])
            else:
                result, _ = run(["/usr/bin/systemctl", "reboot"])
            action["accepted"] = result == 0
        save(path, state)
        return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = tick(apply=args.apply)
    except BlockingIOError:
        result = {"status": "busy"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
