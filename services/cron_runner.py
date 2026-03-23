# services/cron_runner.py
"""
Cron job execution engine for Curie AI.

Reads job definitions from ~/.curie/cron.json, evaluates which jobs are due,
and executes each due job by sending its prompt through ChatWorkflow.  Results
are delivered to the MASTER_USER_ID via the proactive-messaging connectors.

Supported schedule formats
--------------------------
Standard 5-field cron expression:
    */5 * * * *      every 5 minutes
    0 9 * * 1-5      09:00 on weekdays
    30 8 1 * *       08:30 on the 1st of every month

Named macros:
    @reboot          runs once when the daemon starts (one-off on startup)
    @hourly          0 * * * *
    @daily           0 0 * * *
    @midnight        0 0 * * *
    @weekly          0 0 * * 0
    @monthly         0 0 1 * *
    @yearly          0 0 1 1 *
    @annually        0 0 1 1 *

Simple interval shortcuts (Curie extension):
    @every_5m        every 5 minutes
    @every_30m       every 30 minutes
    @every_1h        every hour
    @every_6h        every 6 hours
    @every_12h       every 12 hours
    @every_1d        every day

How it works
------------
The runner starts a background thread that wakes every ``_CHECK_INTERVAL``
seconds (default: 30, configurable via ``CRON_CHECK_INTERVAL`` env var) and:
1. Loads the job file.
2. For each enabled job it calls ``_is_due(job, now)`` which compares the last
   run timestamp against the cron expression to decide whether to fire.
3. Due jobs are executed via ``_run_job(job, workflow, connectors)``:
   a. ``ChatWorkflow.process_message()`` is called with the job prompt.
   b. The result is sent to the master user through any available connector.
   c. ``last_run`` is updated on disk.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.chat_workflow import ChatWorkflow

logger = logging.getLogger(__name__)

# How often the runner wakes to check for due jobs (seconds).
# Minimum effective resolution is one minute because cron expressions are
# minute-granular, so checking every 30s is safe and responsive.
_CHECK_INTERVAL = int(os.getenv("CRON_CHECK_INTERVAL", "30"))

# ─── Named macro expansions ──────────────────────────────────────────────────

_MACROS: dict[str, str] = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

# Interval shortcuts: @every_<N><unit>  e.g. @every_5m, @every_2h, @every_1d
_INTERVAL_RE = re.compile(
    r"^@every_(\d+)(m(?:in(?:ute)?s?)?|h(?:ours?)?|d(?:ays?)?)$",
    re.IGNORECASE,
)


# ─── Cron expression parser ───────────────────────────────────────────────────


def _expand_macro(schedule: str) -> str:
    """Expand named macro or @every_Xu shortcut to a 5-field expression or
    return the original string if it's already a 5-field expression.
    Raises ``ValueError`` for unrecognised macros."""
    s = schedule.strip().lower()
    if s in _MACROS:
        return _MACROS[s]
    m = _INTERVAL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)[0]  # 'm', 'h', or 'd'
        if unit == "m":
            if n < 1 or n > 59:
                raise ValueError(f"Interval minutes must be 1-59, got {n}")
            return f"*/{n} * * * *"
        if unit == "h":
            if n < 1 or n > 23:
                raise ValueError(f"Interval hours must be 1-23, got {n}")
            return f"0 */{n} * * *"
        if unit == "d":
            if n < 1:
                raise ValueError(f"Interval days must be >= 1, got {n}")
            return f"0 0 */{n} * *"
    # Must be a raw 5-field expression
    parts = schedule.strip().split()
    if len(parts) == 5:
        return schedule.strip()
    raise ValueError(
        f"Unrecognised schedule {schedule!r}. "
        "Use a 5-field cron expression, a macro (@daily, @hourly…), "
        "or an interval (@every_5m, @every_2h, @every_1d)."
    )


def _parse_field(field: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field (e.g. '*/5', '1-5', '1,3,5', '*') and return
    the set of matching integers within [min_val, max_val]."""
    if field == "*":
        return set(range(min_val, max_val + 1))

    result: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if range_part == "*":
                start, end = min_val, max_val
            elif "-" in range_part:
                start_s, end_s = range_part.split("-", 1)
                start, end = int(start_s), int(end_s)
            else:
                start = end = int(range_part)
            result.update(range(start, end + 1, step))
        elif "-" in part:
            start_s, end_s = part.split("-", 1)
            result.update(range(int(start_s), int(end_s) + 1))
        else:
            result.add(int(part))

    return {v for v in result if min_val <= v <= max_val}


def cron_matches(schedule: str, dt: datetime) -> bool:
    """Return True if *dt* (minute-granular) matches the *schedule* expression.

    Follows the Vixie/standard cron convention for day-of-month (DOM) and
    day-of-week (DOW):

    - If *both* fields are unrestricted (``*``): a date must satisfy both
      (i.e. AND — normal behaviour).
    - If *at least one* field is restricted (not ``*``): a date must satisfy
      DOM **or** DOW (OR semantics).  This matches how crontab(5) specifies
      expressions like ``0 0 1 * 0`` should fire on the 1st of the month
      *and* on every Sunday.

    Raises ``ValueError`` if the schedule cannot be parsed.
    """
    if schedule.strip().lower() == "@reboot":
        return False  # handled separately at startup

    expr = _expand_macro(schedule)
    minute_f, hour_f, dom_f, month_f, dow_f = expr.split()

    minutes = _parse_field(minute_f, 0, 59)
    hours = _parse_field(hour_f, 0, 23)
    doms = _parse_field(dom_f, 1, 31)
    months = _parse_field(month_f, 1, 12)
    dows = _parse_field(dow_f, 0, 6)  # 0=Sunday

    # isoweekday: Mon=1…Sun=7; cron: Sun=0…Sat=6
    iso_dow = dt.isoweekday() % 7  # Mon=1→1, Sun=7→0

    # Standard cron OR semantics: when both DOM and DOW are restricted,
    # the expression fires when EITHER matches.
    dom_restricted = dom_f != "*"
    dow_restricted = dow_f != "*"
    if dom_restricted and dow_restricted:
        day_match = (dt.day in doms) or (iso_dow in dows)
    else:
        day_match = (dt.day in doms) and (iso_dow in dows)

    return (
        dt.minute in minutes and dt.hour in hours and day_match and dt.month in months
    )


def _is_due(job: dict, now: datetime) -> bool:
    """Return True if *job* should fire at *now*.

    A job fires when:
    - it is enabled, and
    - ``cron_matches(schedule, now)`` is True, and
    - it has not already fired in this minute (i.e. last_run is from an earlier
      minute than now, so we compare at minute granularity).
    """
    if not job.get("enabled", True):
        return False

    schedule = job.get("schedule", "")
    if schedule.strip().lower() == "@reboot":
        return False  # handled at startup

    try:
        if not cron_matches(schedule, now):
            return False
    except ValueError as e:
        logger.warning(
            "Cron job %r has invalid schedule %r: %s", job.get("id"), schedule, e
        )
        return False

    # Avoid double-firing in the same minute
    last_run_str = job.get("last_run")
    if last_run_str:
        try:
            last_run = datetime.fromisoformat(last_run_str)
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            # Same minute already fired
            if (
                last_run.year,
                last_run.month,
                last_run.day,
                last_run.hour,
                last_run.minute,
            ) == (now.year, now.month, now.day, now.hour, now.minute):
                return False
        except (ValueError, TypeError):
            pass  # Bad timestamp → treat as never run

    return True


# ─── Job execution ────────────────────────────────────────────────────────────


async def _run_job(
    job: dict,
    workflow: "ChatWorkflow",
    connectors: dict,
    master_internal_id: Optional[str],
    master_platform: str,
    master_external_id: Optional[str],
) -> None:
    """Execute a single cron job: run the prompt through ChatWorkflow and
    deliver the response to the master user."""
    job_id = job.get("id", "?")
    prompt = job.get("prompt", "")
    logger.info("Cron: firing job %r  prompt=%r", job_id, prompt[:60])

    try:
        normalized_input = {
            "platform": master_platform,
            "external_user_id": master_external_id or "cron",
            "external_chat_id": master_external_id or "cron",
            "message_id": str(uuid.uuid4()),
            "text": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "internal_id": master_internal_id,
        }
        result = await workflow.process_message(normalized_input)
        response = (
            result.get("text", "[no response]")
            if isinstance(result, dict)
            else str(result)
        )
    except Exception as e:
        logger.error(
            "Cron: job %r failed during ChatWorkflow: %s", job_id, e, exc_info=True
        )
        response = f"[Cron job error: {e}]"

    # Deliver the response through the connector matching master_platform
    delivered = False
    if master_external_id:
        connector = connectors.get(master_platform)
        if connector is not None:
            try:
                send = getattr(connector, "send_message", None)
                if send:
                    await send(master_external_id, f"⏰ Cron job {job_id}:\n{response}")
                    delivered = True
            except Exception as e:
                logger.warning("Cron: could not deliver via %s: %s", master_platform, e)

    if not delivered:
        logger.info(
            "Cron: job %r result (no connector): %s", job_id, str(response)[:200]
        )


# ─── Cron runner service ──────────────────────────────────────────────────────


class CronRunner:
    """
    Background thread that checks for due cron jobs and fires them.

    Usage (inside the daemon or ProactiveMessagingService)::

        runner = CronRunner(workflow=workflow, connectors=connectors)
        runner.start()
        # …
        runner.stop()

    The *workflow* and *connectors* are the same objects used by the rest of
    the daemon, so cron jobs execute with the same ChatWorkflow context.
    """

    def __init__(
        self,
        workflow: "ChatWorkflow",
        connectors: dict | None = None,
    ) -> None:
        self.workflow = workflow
        self.connectors = connectors or {}
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._reboot_jobs_fired = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(
            target=self._loop, name="curie-cron-runner", daemon=True
        )
        self._thread.start()
        logger.info("CronRunner started (check_interval=%ds)", _CHECK_INTERVAL)

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=_CHECK_INTERVAL + 5)
            if self._thread.is_alive():
                logger.warning(
                    "CronRunner thread did not stop within %ds; thread still alive",
                    _CHECK_INTERVAL + 5,
                )
        logger.info("CronRunner stopped")

    def _master_info(self) -> tuple[Optional[str], str, Optional[str]]:
        """Return (internal_id, platform, external_id) for the master user."""
        master_id = os.getenv("MASTER_USER_ID") or None
        if not master_id:
            return None, "api", None

        # Try to find an external ID for the master user on any connected platform
        try:
            from memory.database import get_pg_conn  # noqa: PLC0415
            from psycopg2 import sql as _sql  # noqa: PLC0415

            with get_pg_conn() as conn:
                cur = conn.cursor()
                for platform in ["telegram", "discord", "api"]:
                    col = f"{platform}_id"
                    cur.execute(
                        _sql.SQL("SELECT {} FROM users WHERE internal_id = %s").format(
                            _sql.Identifier(col)
                        ),
                        (master_id,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        ids = row[0]
                        ext_id = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
                        return master_id, platform, ext_id
        except Exception as e:
            logger.debug("CronRunner: could not look up master external ID: %s", e)

        return master_id, "api", None

    def _fire_reboot_jobs(self) -> None:
        """Run @reboot jobs once on startup."""
        from cli.cron import get_jobs, _save  # noqa: PLC0415

        jobs = get_jobs()
        reboot_jobs = [
            j
            for j in jobs
            if j.get("schedule", "").strip().lower() == "@reboot"
            and j.get("enabled", True)
        ]
        if not reboot_jobs:
            return

        master_id, master_platform, master_ext = self._master_info()
        import asyncio  # noqa: PLC0415

        for job in reboot_jobs:
            logger.info("CronRunner: firing @reboot job %r", job.get("id"))
            try:
                asyncio.run(
                    _run_job(
                        job,
                        self.workflow,
                        self.connectors,
                        master_id,
                        master_platform,
                        master_ext,
                    )
                )
                # Update last_run
                job["last_run"] = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                logger.error("CronRunner: @reboot job %r error: %s", job.get("id"), e)
        _save(jobs)

    def _loop(self) -> None:
        # Fire @reboot jobs on first iteration
        if not self._reboot_jobs_fired:
            try:
                self._fire_reboot_jobs()
            except Exception as e:
                logger.error("CronRunner: error in _fire_reboot_jobs: %s", e)
            self._reboot_jobs_fired = True

        while self.running:
            try:
                self._tick()
            except Exception as e:
                logger.error(
                    "CronRunner: unexpected error in tick: %s", e, exc_info=True
                )
            time.sleep(_CHECK_INTERVAL)

    def _tick(self) -> None:
        """One check cycle: load jobs, find due ones, fire them."""
        import asyncio  # noqa: PLC0415
        from cli.cron import _load, _save  # noqa: PLC0415

        now = datetime.now(timezone.utc)
        jobs = _load()
        master_id, master_platform, master_ext = self._master_info()

        fired_any = False
        for job in jobs:
            if _is_due(job, now):
                try:
                    asyncio.run(
                        _run_job(
                            job,
                            self.workflow,
                            self.connectors,
                            master_id,
                            master_platform,
                            master_ext,
                        )
                    )
                    job["last_run"] = now.isoformat()
                    fired_any = True
                    logger.info("CronRunner: job %r fired successfully", job.get("id"))
                except Exception as e:
                    logger.error(
                        "CronRunner: error running job %r: %s",
                        job.get("id"),
                        e,
                        exc_info=True,
                    )

        if fired_any:
            _save(jobs)
