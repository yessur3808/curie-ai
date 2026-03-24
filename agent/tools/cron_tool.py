# agent/tools/cron_tool.py
"""
Cron tool — schedule agent prompts via the Curie cron subsystem.

Wraps the existing ``cli.cron`` module so agents can create, list, and
remove scheduled jobs through natural language.

Natural-language triggers
--------------------------
  "schedule a job every hour to check server health"
  "cron add every 30 minutes: summarise recent logs"
  "list cron jobs"
  "remove cron job abc123"
  "disable cron job abc123"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "cron_tool"

_CRON_KEYWORDS = re.compile(
    r"\bcron\b|\bschedule(d)?\s+(job|task|prompt)\b|"
    r"\bevery\s+\d+\s*(minute|hour|day|week|month|min|hr)\b|"
    r"\b@(hourly|daily|weekly|monthly|reboot)\b",
    re.IGNORECASE,
)

# Mapping of natural-language intervals to cron expressions
_INTERVAL_MAP = [
    (re.compile(r"every\s+(\d+)\s*min(?:ute)?s?", re.I), lambda m: f"*/{m.group(1)} * * * *"),
    (re.compile(r"every\s+(\d+)\s*h(?:our)?s?", re.I), lambda m: f"0 */{m.group(1)} * * *"),
    (re.compile(r"every\s+hour", re.I), lambda _: "0 * * * *"),
    (re.compile(r"every\s+day|@daily|@midnight", re.I), lambda _: "0 0 * * *"),
    (re.compile(r"every\s+week|@weekly", re.I), lambda _: "0 0 * * 0"),
    (re.compile(r"every\s+month|@monthly", re.I), lambda _: "0 0 1 * *"),
    (re.compile(r"on\s+reboot|@reboot", re.I), lambda _: "@reboot"),
]


def is_tool_query(message: str) -> bool:
    return bool(_CRON_KEYWORDS.search(message))


def _parse_schedule(message: str) -> Optional[str]:
    """Extract a cron expression from a natural-language string."""
    for pattern, builder in _INTERVAL_MAP:
        m = pattern.search(message)
        if m:
            return builder(m)
    # Accept a raw 5-field expression
    m = re.search(r"(\S+\s+\S+\s+\S+\s+\S+\s+\S+)", message)
    if m:
        candidate = m.group(1)
        if re.match(r"^[\d\*\/\,\-]+$", candidate.replace(" ", "")):
            return candidate
    return None


def _parse_prompt(message: str) -> Optional[str]:
    """Extract the task prompt from add/schedule commands."""
    for kw in ("to check", "to summarise", "to summarize", "to run", "to do", "to:", ":"):
        idx = message.lower().find(kw)
        if idx != -1:
            candidate = message[idx + len(kw):].strip().strip("\"'")
            if candidate:
                return candidate
    # Fallback: take everything after "job" / "task" keyword
    m = re.search(r"(?:job|task|prompt)\s+(.+)$", message, re.I)
    if m:
        return m.group(1).strip().strip("\"'")
    return None


async def handle_tool_query(
    message: str,
    **_kwargs,
) -> Optional[str]:
    if not is_tool_query(message):
        return None

    try:
        from cli.cron import (  # noqa: PLC0415
            add_job,
            remove_job,
            get_jobs,
        )
    except ImportError:
        return "⚠️ Cron module unavailable."

    msg = message.strip()

    # --- list ---
    if re.search(r"\b(list|show|display|view)\b.*(cron|scheduled|job)", msg, re.I):
        jobs = get_jobs()
        if not jobs:
            return "⏰ No scheduled jobs. Use *schedule a job every hour to …* to add one."
        lines = ["⏰ *Scheduled Jobs:*"]
        for j in jobs:
            status = "✅" if j.get("enabled") else "⏸"
            lines.append(
                f"  {status} `{j['id']}` — `{j['schedule']}` — {j['prompt'][:60]}"
            )
        return "\n".join(lines)

    # --- remove / disable / enable ---
    m = re.search(
        r"\b(remove|delete|disable|enable)\b.*(cron\s+)?job\s+[\"']?(?P<id>[a-z0-9_\-]+)[\"']?",
        msg,
        re.I,
    )
    if m:
        action = m.group(1).lower()
        job_id = m.group("id")
        if action in ("remove", "delete"):
            ok = remove_job(job_id)
            return f"⏰ Cron job **{job_id}** {'removed' if ok else 'not found'}."
        if action == "disable":
            from cli.cron import _load, _save  # noqa: PLC0415
            jobs = _load()
            for j in jobs:
                if j["id"] == job_id:
                    j["enabled"] = False
                    _save(jobs)
                    return f"⏰ Cron job **{job_id}** disabled."
            return f"⏰ Job **{job_id}** not found."
        if action == "enable":
            from cli.cron import _load, _save  # noqa: PLC0415
            jobs = _load()
            for j in jobs:
                if j["id"] == job_id:
                    j["enabled"] = True
                    _save(jobs)
                    return f"⏰ Cron job **{job_id}** enabled."
            return f"⏰ Job **{job_id}** not found."

    # --- add / schedule ---
    if re.search(r"\b(add|create|schedule|set up)\b", msg, re.I):
        schedule = _parse_schedule(msg)
        prompt = _parse_prompt(msg)
        if not schedule:
            return (
                "⏰ I couldn't parse a schedule from your request.  "
                "Try: *schedule a job every hour to check server health*"
            )
        if not prompt:
            return (
                "⏰ I couldn't determine what prompt to run.  "
                "Try: *schedule a job every hour to check server health*"
            )
        job = add_job(schedule, prompt)
        return (
            f"⏰ Cron job **{job['id']}** scheduled.\n"
            f"  Schedule: `{schedule}`\n"
            f"  Prompt: {prompt}"
        )

    return None
