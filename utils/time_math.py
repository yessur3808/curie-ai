"""Small deterministic calculations that should never be guessed by an LLM."""

from __future__ import annotations

from datetime import datetime
import re

_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b", re.I)


def _minutes(hour: str, minute: str | None, meridiem: str) -> int:
    value = int(hour) % 12
    if meridiem.casefold().startswith("p"):
        value += 12
    return value * 60 + int(minute or 0)


def _elapsed(start: int, end: int) -> int:
    return end - start if end >= start else end + 24 * 60 - start


def calculate_split_sleep(user_text: str, now: datetime) -> dict | None:
    """Calculate 'slept at A, woke at B, slept at C, woke now' exactly."""
    lowered = user_text.casefold()
    if not ("slept" in lowered and "woke" in lowered and "now" in lowered):
        return None
    clocks = [_minutes(*match.groups()) for match in _CLOCK.finditer(user_text)]
    if len(clocks) != 3:
        return None
    first, second, third = clocks
    current = now.hour * 60 + now.minute
    intervals = [_elapsed(first, second), _elapsed(third, current)]
    total = sum(intervals)
    if not (0 < total <= 24 * 60):
        return None
    return {"intervals": intervals, "total_minutes": total}


def format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if remainder:
        parts.append(f"{remainder} minute{'s' if remainder != 1 else ''}")
    return " and ".join(parts) or "0 minutes"


def split_sleep_reply(user_text: str, now: datetime) -> str | None:
    result = calculate_split_sleep(user_text, now)
    if not result:
        return None
    first, second = result["intervals"]
    total = result["total_minutes"]
    return (
        f"You slept {format_duration(first)} the first time and {format_duration(second)} the second time, "
        f"so the total is {format_duration(total)}. Voilà, that is the checked calculation."
    )
