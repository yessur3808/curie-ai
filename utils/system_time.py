# utils/system_time.py
"""
Authoritative system-time source with optional internet verification.

How it works
------------
1. The system clock is always the **primary** source — it works offline.
2. On the first call (and after every ``_CACHE_TTL`` seconds) the module
   optionally fires a single non-blocking HTTP request to a public NTP-backed
   REST API (worldtimeapi.org).  If that succeeds, the UTC offset reported by
   the internet is compared with the local system clock.  A warning is logged
   if the drift exceeds ``DRIFT_WARN_SECONDS`` (default 5 s).
3. Results are cached so the internet check does NOT happen on every prompt
   build — it happens at most once every ``_CACHE_TTL`` seconds (default 300).
4. The internet check can be disabled entirely with
   ``ENABLE_TIME_VERIFICATION=false`` in the environment.

Exported API
------------
get_verified_now(tz=None) -> datetime
    Return the current time as a timezone-aware datetime.
    tz: a pytz timezone object or None (returns UTC-aware).

is_internet_time_available() -> bool
    Return True if the last internet verification succeeded.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone as _stdtz
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------

_ENABLED: bool = os.getenv("ENABLE_TIME_VERIFICATION", "true").strip().lower() != "false"

# Public time API — returns JSON with a "utc_datetime" field.
# worldtimeapi.org is used because it requires no API key and is NTP-backed.
_TIME_API_URL: str = os.getenv(
    "TIME_API_URL",
    "https://worldtimeapi.org/api/timezone/UTC",
)

# Short connect + read timeout; if the internet is unavailable we never want
# the assistant to hang waiting for a time check.
_HTTP_TIMEOUT: float = float(os.getenv("TIME_API_TIMEOUT", "3.0"))

# How many seconds of drift between system clock and internet time triggers a
# warning in the log.
DRIFT_WARN_SECONDS: float = float(os.getenv("TIME_DRIFT_WARN_SECONDS", "5.0"))

# How long (seconds) to cache the internet check result before trying again.
_CACHE_TTL: float = float(os.getenv("TIME_CACHE_TTL", "300"))

# ---------------------------------------------------------------------------
# Thread-safe cache
# ---------------------------------------------------------------------------

_lock = threading.Lock()

# Monotonic timestamp of the last internet check attempt.
_last_check_mono: float = 0.0

# True if the most recent internet check succeeded (HTTP 200 + parseable time field).
# Note: _internet_ok is set True even when drift exceeds DRIFT_WARN_SECONDS — that
# threshold only triggers a log warning; the internet time source is still considered
# available.  Set to False on any network or parse error.
_internet_ok: bool = False

# Measured UTC offset (system_utc - internet_utc) from the last check, in seconds.
_last_drift_seconds: float = 0.0


def _check_internet_time() -> None:
    """
    Fire a single HTTP request to the time API, compare with system clock,
    and update the module-level cache.  Never raises — all errors are caught.

    This function runs in a background daemon thread.  All writes to the
    shared globals ``_internet_ok`` and ``_last_drift_seconds`` are protected
    by ``_lock`` so readers always see a consistent snapshot.
    Note: ``_last_check_mono`` is intentionally set *before* this thread
    starts (inside ``_maybe_refresh``) to prevent stampedes; we do not
    update it again here.
    """
    global _internet_ok, _last_drift_seconds

    system_utc_before = datetime.now(_stdtz.utc)

    try:
        import httpx  # available in requirements.txt

        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.get(_TIME_API_URL)
            resp.raise_for_status()
            data = resp.json()

        system_utc_after = datetime.now(_stdtz.utc)

        # Parse the internet UTC time
        raw_utc: Optional[str] = data.get("utc_datetime") or data.get("datetime")
        if not raw_utc:
            logger.warning("system_time: time API returned no utc_datetime field")
            with _lock:
                _internet_ok = False
            return

        # Strip sub-second precision and trailing 'Z'/'offset' for fromisoformat
        # worldtimeapi returns e.g. "2026-03-20T05:21:25.550123+00:00"
        internet_utc = datetime.fromisoformat(raw_utc.replace("Z", "+00:00"))
        if internet_utc.tzinfo is None:
            internet_utc = internet_utc.replace(tzinfo=_stdtz.utc)

        # Estimate round-trip midpoint
        midpoint = system_utc_before + (system_utc_after - system_utc_before) / 2
        drift = (midpoint - internet_utc).total_seconds()

        if abs(drift) > DRIFT_WARN_SECONDS:
            logger.warning(
                "system_time: system clock is %.1f s %s from internet time — "
                "check your system clock / NTP configuration.",
                abs(drift),
                "ahead of" if drift > 0 else "behind",
            )
        else:
            logger.debug(
                "system_time: clock verified OK (drift=%.3f s)", drift
            )

        with _lock:
            _internet_ok = True
            _last_drift_seconds = drift

    except Exception as exc:
        logger.debug("system_time: internet time check failed (%s) — using system clock", exc)
        with _lock:
            _internet_ok = False


def _maybe_refresh() -> None:
    """Trigger an internet check if the cache has expired (non-blocking).

    Sets ``_last_check_mono`` under ``_lock`` *before* launching the
    background thread so that concurrent callers in other threads see the
    updated timestamp immediately and do not start additional threads for
    the same TTL window (prevents a stampede on TTL expiry).
    """
    if not _ENABLED:
        return
    now = time.monotonic()
    global _last_check_mono
    with _lock:
        # Re-check under the lock to avoid starting multiple refresh threads.
        if now - _last_check_mono < _CACHE_TTL:
            return
        # Mark as recently attempted before starting the thread so other
        # callers won't spawn additional concurrent checks for this window.
        _last_check_mono = now
    # Run in a daemon thread so it never blocks the caller.
    t = threading.Thread(target=_check_internet_time, daemon=True, name="time-verify")
    t.start()


def get_verified_now(tz: Optional[pytz.BaseTzInfo] = None) -> datetime:
    """Return the current time from the **system clock**, after optionally
    triggering a background internet verification.

    Parameters
    ----------
    tz : pytz timezone or None
        If supplied, the returned datetime is in that timezone.
        If None, the returned datetime is UTC-aware.

    Returns
    -------
    datetime
        Always timezone-aware.  The system clock is the authoritative source;
        the internet check only logs a warning if drift is detected.
    """
    _maybe_refresh()

    if tz is None:
        return datetime.now(_stdtz.utc)
    return datetime.now(tz)


def is_internet_time_available() -> bool:
    """Return True if the most recent internet verification succeeded."""
    with _lock:
        return _internet_ok


def get_time_source_label() -> str:
    """Return a short human-readable label for the time source used.

    Used in the [USER CONTEXT] block of the prompt so the model (and user)
    knows whether the time has been independently verified.
    """
    if not _ENABLED:
        return "system clock"
    with _lock:
        ok = _internet_ok
        checked = _last_check_mono
    if ok:
        return "system clock (internet-verified)"
    if checked == 0.0:
        # No check has been attempted yet — first call is always system-only
        return "system clock"
    return "system clock (internet unavailable)"
