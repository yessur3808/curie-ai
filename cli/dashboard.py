"""Live operational dashboard for Curie assistant instances."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - handled by the command entry point
    psutil = None  # type: ignore[assignment]

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None  # type: ignore[assignment]

try:
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover
    Console = None  # type: ignore[assignment,misc]

from cli import daemon


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTANCE_DIR = REPO_ROOT / "instances"
PERSONA_DIR = REPO_ROOT / "assets" / "personality"
_processes: dict[int, Any] = {}


def _fmt_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def discover_instances() -> list[str]:
    """Return configured and previously-started instance names."""
    names = {"default"}
    if INSTANCE_DIR.is_dir():
        names.update(path.stem.casefold() for path in INSTANCE_DIR.glob("*.env"))
    if daemon.CURIE_DIR.is_dir():
        for pattern, suffix in (
            ("instance-*.pid", ".pid"),
            ("instance-*-daemon-state.json", "-daemon-state.json"),
        ):
            for path in daemon.CURIE_DIR.glob(pattern):
                names.add(path.name.removeprefix("instance-").removesuffix(suffix))
    return sorted(names, key=lambda name: (name != "default", name))


def _instance_env(name: str) -> dict[str, str]:
    """Resolve shared .env plus a named overlay without mutating os.environ."""
    values: dict[str, str] = {}
    if dotenv_values is not None:
        base = REPO_ROOT / ".env"
        if base.is_file():
            values.update(
                {k: v for k, v in dotenv_values(base).items() if v is not None}
            )
    # Match daemon startup precedence: shell environment beats the shared file,
    # while a named instance overlay is authoritative for that instance.
    for key in (
        "PERSONA_FILE",
        "LLM_PROVIDER_PRIORITY",
        "LLM_MODELS",
        "LLM_GENERAL_MODEL",
        "OPENAI_MODEL",
        "ANTHROPIC_MODEL",
        "GEMINI_MODEL",
        "LLM_ACCELERATOR_MODE",
        "LLM_NPU_MODEL",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    overlay = INSTANCE_DIR / f"{name}.env"
    if name != "default" and dotenv_values is not None and overlay.is_file():
        values.update(
            {k: v for k, v in dotenv_values(overlay).items() if v is not None}
        )
    return values


def _persona_name(filename: str) -> str:
    if filename.casefold() == "all":
        return "All personas"
    filename = filename or "personality.json"
    if not filename.endswith(".json"):
        filename += ".json"
    try:
        data = json.loads(
            (PERSONA_DIR / Path(filename).name).read_text(encoding="utf-8")
        )
        return str(data.get("name") or Path(filename).stem.title())
    except (OSError, json.JSONDecodeError):
        return Path(filename).stem.title()


def _model_summary(env: dict[str, str]) -> str:
    providers = [
        p.strip().lower()
        for p in env.get("LLM_PROVIDER_PRIORITY", "llama.cpp").split(",")
        if p.strip()
    ]
    models: list[str] = []
    mapping = {
        "openai": ("OPENAI_MODEL", "gpt-3.5-turbo"),
        "anthropic": ("ANTHROPIC_MODEL", "claude-3-haiku-20240307"),
        "gemini": ("GEMINI_MODEL", "gemini-1.5-flash"),
        "llama.cpp": ("LLM_GENERAL_MODEL", ""),
    }
    for provider in providers:
        key, fallback = mapping.get(provider, ("", ""))
        model = env.get(key, fallback) if key else ""
        if provider == "llama.cpp" and not model:
            model = env.get("LLM_MODELS", "local GGUF").split(",")[0].strip()
        models.append(f"{provider}: {model or 'configured default'}")
    return " → ".join(models) or "not configured"


def _format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    days, remainder = divmod(max(0, seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


def _runtime_path(name: str, kind: str) -> Path:
    filename = f"{kind}.json" if name == "default" else f"instance-{name}-{kind}.json"
    return daemon.CURIE_DIR / filename


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _task_status(name: str) -> tuple[str, int]:
    tasks = _read_json(_runtime_path(name, "tasks")).get("tasks", {})
    running = [task for task in tasks.values() if task.get("status") == "running"]
    if not running:
        return "idle", 0
    newest = max(running, key=lambda task: task.get("started_at", 0))
    description = str(newest.get("description", "working")).replace("\n", " ")
    return description[:32], len(running)


def _connectors(name: str, env: dict[str, str]) -> str:
    state = daemon.read_daemon_state(name)
    flags = set(state.get("cmd", [])[2:])
    known = {
        "api": "RUN_API",
        "telegram": "RUN_TELEGRAM",
        "discord": "RUN_DISCORD",
        "slack": "RUN_SLACK",
        "whatsapp": "RUN_WHATSAPP",
        "signal": "RUN_SIGNAL",
    }
    enabled = []
    for connector, key in known.items():
        if (
            "--all" in flags
            or f"--{connector}" in flags
            or env.get(key, "false").lower() == "true"
        ):
            enabled.append("api:8000" if connector == "api" else connector)
    return ", ".join(enabled) or "none"


def _recent_log(name: str, limit: int = 2) -> tuple[int, list[str]]:
    log_file = daemon._instance_paths(name)[1]
    try:
        if time.time() - log_file.stat().st_mtime > 300:
            return 0, []
        lines = log_file.read_text(errors="replace").splitlines()[-200:]
    except OSError:
        return 0, []
    errors = [
        line.strip()
        for line in lines
        if "error" in line.casefold() or "exception" in line.casefold()
    ]
    return len(errors), [line[-140:] for line in errors[-limit:]]


def _process_metrics(pid: int | None, sample: bool = False) -> tuple[str, str, int]:
    if psutil is None or pid is None:
        return "—", "—", 0
    try:
        proc = _processes.setdefault(pid, psutil.Process(pid))
        cpu = proc.cpu_percent(interval=0.1 if sample else None)
        memory = proc.memory_info().rss
        threads = proc.num_threads()
        return f"{cpu:.1f}%", _fmt_bytes(memory), threads
    except (psutil.Error, OSError):
        _processes.pop(pid, None)
        return "—", "—", 0


def collect_instances(sample: bool = False) -> list[dict[str, Any]]:
    rows = []
    for name in discover_instances():
        try:
            status = daemon.get_status(name)
        except OSError:
            # Status normally cleans stale PID files. Read-only/home-mounted
            # environments should still be able to inspect the dashboard.
            pid = daemon._read_pid(name)
            running = bool(pid and daemon._is_process_running(pid))
            _, log_file, _ = daemon._instance_paths(name)
            status = {
                "running": running,
                "pid": pid if running else None,
                "uptime_seconds": None,
                "log_file": str(log_file),
                "instance": name,
            }
        env = _instance_env(name)
        cpu, memory, threads = _process_metrics(status.get("pid"), sample=sample)
        activity, active_tasks = _task_status(name)
        telemetry = _read_json(_runtime_path(name, "telemetry"))
        log_errors, recent_errors = _recent_log(name)
        telemetry_stale = time.time() - telemetry.get("updated_at", 0) > 300
        if not status["running"]:
            health = "stopped"
        elif log_errors:
            health = "degraded"
        elif status.get("uptime_seconds", 0) < 10:
            health = "starting"
        else:
            health = "healthy"
        rows.append(
            {
                **status,
                "persona": _persona_name(env.get("PERSONA_FILE", "personality.json")),
                "providers": env.get("LLM_PROVIDER_PRIORITY", "llama.cpp"),
                "models": _model_summary(env),
                "accelerator": env.get("LLM_ACCELERATOR_MODE", "auto"),
                "cpu": cpu,
                "memory": memory,
                "threads": threads,
                "health": health,
                "uptime": _format_uptime(status.get("uptime_seconds")),
                "connectors": _connectors(name, env),
                "activity": activity,
                "active_tasks": active_tasks,
                "telemetry": (
                    {} if telemetry_stale and not status["running"] else telemetry
                ),
                "log_errors": log_errors,
                "recent_errors": recent_errors,
            }
        )
    return rows


def _nvidia_rows() -> list[tuple[str, str, str, str]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 4:
                name, util, used, total = parts
                rows.append(("GPU", name, f"{util}%", f"{used} / {total} MiB"))
    return rows


def _accelerators() -> list[tuple[str, str, str, str]]:
    rows = _nvidia_rows()
    if not rows:
        render_nodes = sorted(Path("/dev/dri").glob("renderD*"))
        rows.append(
            (
                "GPU",
                render_nodes[0].name if render_nodes else "not detected",
                "n/a",
                "utilization unavailable",
            )
        )
    npu_device = Path("/dev/accel/accel0")
    rows.append(
        (
            "NPU",
            "available" if npu_device.exists() else "not detected",
            "n/a",
            "driver does not expose portable utilization",
        )
    )
    return rows


def build_dashboard(sample: bool = False) -> "Group":
    instances = collect_instances(sample=sample)
    instance_table = Table(box=box.ROUNDED, expand=True, header_style="bold cyan")
    instance_table.add_column("Instance", no_wrap=True)
    instance_table.add_column("State", no_wrap=True)
    instance_table.add_column("Uptime", no_wrap=True)
    instance_table.add_column("Personality")
    instance_table.add_column("Model")
    instance_table.add_column("Connectors")
    instance_table.add_column("Current work")
    instance_table.add_column("CPU", justify="right")
    instance_table.add_column("RAM", justify="right")
    for row in instances:
        styles = {
            "healthy": "green",
            "starting": "yellow",
            "degraded": "bold red",
            "stopped": "dim",
        }
        state = Text(
            f"● {row['health']}" if row["running"] else "○ stopped",
            style=styles[row["health"]],
        )
        model = row["telemetry"].get("last_model") or row["models"]
        instance_table.add_row(
            row["instance"],
            state,
            row["uptime"],
            row["persona"],
            model,
            row["connectors"],
            row["activity"],
            row["cpu"],
            row["memory"],
        )

    inference = Table(box=box.ROUNDED, expand=True, header_style="bold cyan")
    inference.add_column("Instance")
    inference.add_column("RPM", justify="right")
    inference.add_column("Latency avg / p95", justify="right")
    inference.add_column("First token", justify="right")
    inference.add_column("Tokens/s", justify="right")
    inference.add_column("Context", justify="right")
    inference.add_column("Queue", justify="right")
    inference.add_column("Reloads", justify="right")
    inference.add_column("Errors", justify="right")
    inference.add_column("Fallbacks", justify="right")
    for row in instances:
        telemetry = row["telemetry"]
        latency = telemetry.get("latency", {}).get("total", {})
        latency_text = (
            "—"
            if not latency
            else f"{latency.get('avg_ms', 0):.0f} / {latency.get('p95_ms', 0):.0f} ms"
        )
        managed = telemetry.get("inference", {})
        inference.add_row(
            row["instance"],
            str(telemetry.get("requests_per_minute", "—")),
            latency_text,
            (
                f"{managed['first_token_ms']:.0f} ms"
                if "first_token_ms" in managed
                else "—"
            ),
            str(
                managed.get(
                    "tokens_per_second",
                    telemetry.get("tokens_per_second_estimate", "—"),
                )
            ),
            (
                f"{telemetry['context_used_percent_estimate']}%"
                if "context_used_percent_estimate" in telemetry
                else "—"
            ),
            (
                f"{managed.get('queue_depth', telemetry.get('queue_depth', row['active_tasks']))}"
                f"/{managed.get('queue_capacity', '—')}"
            ),
            str(managed.get("model_reloads", "—")),
            str(max(int(telemetry.get("errors", 0)), row["log_errors"])),
            str(telemetry.get("fallbacks", "—")),
        )

    resources = Table(box=box.ROUNDED, expand=True, header_style="bold cyan")
    resources.add_column("Resource")
    resources.add_column("Device")
    resources.add_column("Utilization", justify="right")
    resources.add_column("Details")
    if psutil is not None:
        vm = psutil.virtual_memory()
        resources.add_row(
            "CPU",
            f"{psutil.cpu_count(logical=False) or '?'} cores / {psutil.cpu_count() or '?'} threads",
            f"{psutil.cpu_percent():.1f}%",
            "host total",
        )
        resources.add_row(
            "Memory",
            _fmt_bytes(vm.total),
            f"{vm.percent:.1f}%",
            f"{_fmt_bytes(vm.used)} used · {_fmt_bytes(vm.available)} available",
        )
    for resource in _accelerators():
        resources.add_row(*resource)
    running = sum(1 for row in instances if row["running"])
    title = f"Curie Operations  •  {running}/{len(instances)} instances running  •  {time.strftime('%Y-%m-%d %H:%M:%S')}"
    recent = []
    for row in instances:
        recent.extend(f"[{row['instance']}] {line}" for line in row["recent_errors"])
    log_text = Text(
        "\n".join(recent[-4:]) if recent else "No recent errors",
        style="red" if recent else "dim green",
    )
    return Group(
        Panel(instance_table, title="Instances", border_style="cyan"),
        Panel(
            inference,
            title="Inference telemetry (token values are estimates)",
            border_style="magenta",
        ),
        Panel(resources, title="Host resources", border_style="blue"),
        Panel(
            log_text, title="Recent errors", border_style="red" if recent else "green"
        ),
        Text(title, justify="center", style="dim"),
    )


def show_dashboard(*, once: bool = False, refresh_rate: float = 1.0) -> None:
    if Console is None or psutil is None:
        print("rich and psutil are required. Run: pip install rich psutil")
        return
    refresh_rate = max(0.1, refresh_rate)
    console = Console()
    psutil.cpu_percent(interval=None)
    if once:
        console.print(build_dashboard(sample=True))
        return
    with Live(
        build_dashboard(),
        console=console,
        refresh_per_second=max(1, 1 / refresh_rate),
        screen=False,
    ) as live:
        try:
            while True:
                time.sleep(refresh_rate)
                live.update(build_dashboard())
        except KeyboardInterrupt:
            pass
