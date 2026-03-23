# cli/cron.py
"""
Cron-style scheduled-prompt management for Curie AI.

Jobs are stored in ~/.curie/cron.json.  Each job has:
  - id: unique slug
  - schedule: cron expression (e.g. "*/5 * * * *") or cron macro/named schedule (e.g. "@hourly", "@daily", "@reboot")
  - prompt: natural-language prompt to send to the agent when the job fires
  - enabled: bool
  - created_at: ISO timestamp

The Curie daemon picks up and executes enabled jobs via the proactive-messaging
service (services/proactive_messaging.py).

Commands:
  curie cron list                         – list all jobs
  curie cron add "SCHEDULE" --prompt P    – add a new job
  curie cron remove <id>                  – remove a job by ID
  curie cron enable <id>                  – enable a disabled job
  curie cron disable <id>                 – disable a job without removing it
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _RICH = True
    _console = Console()
except ImportError:
    _RICH = False
    _console = None

CURIE_DIR = Path.home() / ".curie"
CRON_FILE = CURIE_DIR / "cron.json"


def _p(msg: str) -> None:
    if _RICH and _console:
        _console.print(msg)
    else:
        print(re.sub(r"\[/?[a-zA-Z0-9_ ]+\]", "", msg))


# ─── Persistence helpers ──────────────────────────────────────────────────────


def _load() -> list[dict]:
    if not CRON_FILE.exists():
        return []
    try:
        data = json.loads(CRON_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(jobs: list[dict]) -> None:
    CURIE_DIR.mkdir(parents=True, exist_ok=True)
    CRON_FILE.write_text(json.dumps(jobs, indent=2))


# ─── Public CRUD ─────────────────────────────────────────────────────────────


def get_jobs() -> list[dict]:
    return _load()


def add_job(schedule: str, prompt: str, job_id: Optional[str] = None) -> dict:
    """Add a new cron job and return its record."""
    jobs = _load()
    slug = job_id or re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:32].strip("-") or str(uuid.uuid4())[:8]
    # Ensure uniqueness
    existing_ids = {j["id"] for j in jobs}
    base_slug = slug
    counter = 2
    while slug in existing_ids:
        slug = f"{base_slug}-{counter}"
        counter += 1

    job = {
        "id": slug,
        "schedule": schedule,
        "prompt": prompt,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run": None,
    }
    jobs.append(job)
    _save(jobs)
    return job


def remove_job(job_id: str) -> bool:
    """Remove a job by ID. Returns True if removed."""
    jobs = _load()
    original_count = len(jobs)
    jobs = [j for j in jobs if j["id"] != job_id]
    if len(jobs) == original_count:
        return False
    _save(jobs)
    return True


def set_job_enabled(job_id: str, enabled: bool) -> bool:
    """Enable or disable a job. Returns True if the job was found."""
    jobs = _load()
    for job in jobs:
        if job["id"] == job_id:
            job["enabled"] = enabled
            _save(jobs)
            return True
    return False


# ─── CLI display commands ─────────────────────────────────────────────────────


def cmd_cron_list() -> int:
    """Display all cron jobs."""
    jobs = _load()

    if not jobs:
        _p("[yellow]No cron jobs configured.[/yellow]")
        _p("  Add one with: [bold]curie cron add '*/5 * * * *' --prompt 'Check system health'[/bold]")
        return 0

    if _RICH:
        table = Table(
            title="Curie AI – Scheduled Jobs",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("ID", style="bold", min_width=18)
        table.add_column("Schedule", min_width=16)
        table.add_column("Prompt", min_width=36)
        table.add_column("Enabled", min_width=8)
        table.add_column("Last Run", min_width=20)

        for job in jobs:
            enabled_cell = "[green]yes[/green]" if job.get("enabled") else "[red]no[/red]"
            last_run = job.get("last_run") or "never"
            prompt_preview = (job["prompt"][:55] + "…") if len(job["prompt"]) > 56 else job["prompt"]
            table.add_row(
                job["id"],
                job["schedule"],
                prompt_preview,
                enabled_cell,
                last_run,
            )
        _console.print(table)
    else:
        print(f"{'ID':<22} {'Schedule':<18} {'Enabled':<8} {'Prompt'}")
        print("-" * 80)
        for job in jobs:
            prompt_preview = (job["prompt"][:40] + "…") if len(job["prompt"]) > 41 else job["prompt"]
            print(f"{job['id']:<22} {job['schedule']:<18} {'yes' if job.get('enabled') else 'no':<8} {prompt_preview}")

    return 0


def cmd_cron_add(schedule: str, prompt: str) -> int:
    """Add a new cron job."""
    job = add_job(schedule, prompt)
    _p(f"[green]✅ Cron job added:[/green] [bold]{job['id']}[/bold]")
    _p(f"   Schedule: {job['schedule']}")
    _p(f"   Prompt:   {job['prompt']}")
    return 0


def cmd_cron_remove(job_id: str) -> int:
    """Remove a cron job by ID."""
    if remove_job(job_id):
        _p(f"[green]✅ Cron job removed:[/green] {job_id}")
        return 0
    _p(f"[red]❌ No job found with ID:[/red] {job_id!r}")
    return 1


def cmd_cron_enable(job_id: str, enabled: bool) -> int:
    """Enable or disable a cron job."""
    if set_job_enabled(job_id, enabled):
        state = "enabled" if enabled else "disabled"
        _p(f"[green]✅ Cron job {state}:[/green] {job_id}")
        return 0
    _p(f"[red]❌ No job found with ID:[/red] {job_id!r}")
    return 1
