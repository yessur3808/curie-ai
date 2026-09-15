"""Bounded, opt-in autonomous posting for Curie's own X account.

Manual X writes still require a fresh per-invocation approval.  This service is
an intentionally separate automation mandate: it runs only when the operator
sets both X_AUTOPOST_ENABLED=true and X_AUTOPOST_LIVE=true, supplies explicit
topics, and connects the owner's X account through OAuth.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import logging
import os
import random
import re
import threading
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from agent.persona_contract import apply_persona_contract
from memory.local_store import list_personal_items, save_personal_item

logger = logging.getLogger(__name__)

HARD_DAILY_CAP = 40
_STATE_KIND = "x_autopost_state"
_STATE_SOURCE_ID = "x-autopost-state-v1"
_URL = re.compile(r"https?://|www\.", re.I)
_MENTION = re.compile(r"(?<!\w)@[A-Za-z0-9_]+")
_TAG = re.compile(r"(?<!\w)[#$][A-Za-z][A-Za-z0-9_]*")
_RISKY_ADVICE = re.compile(
    r"\b(?:buy|sell|short|long|bet|wager|ape|price target|guaranteed|"
    r"risk[- ]free|profit|financial advice|medical advice|legal advice)\b",
    re.I,
)
_CURRENT_EVENT = re.compile(
    r"\b(?:breaking|just happened|today's news|trending|election result|"
    r"live score|market is up|market is down)\b",
    re.I,
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _bounded_int(raw: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


@dataclass(frozen=True, slots=True)
class XAutopostConfig:
    enabled: bool
    live: bool
    owner_id: str
    topics: tuple[str, ...]
    timezone_name: str
    window_start_hour: int
    window_end_hour: int
    daily_max: int
    check_interval_seconds: int
    startup_delay_seconds: int

    @classmethod
    def from_env(cls) -> "XAutopostConfig":
        topics = tuple(
            item.strip()[:80]
            for item in os.getenv("X_AUTOPOST_TOPICS", "").split(",")
            if item.strip()
        )[:12]
        timezone_name = (
            os.getenv(
                "X_AUTOPOST_TIMEZONE", os.getenv("DEFAULT_TIMEZONE", "UTC")
            ).strip()
            or "UTC"
        )
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone_name = "UTC"
        return cls(
            enabled=_truthy(os.getenv("X_AUTOPOST_ENABLED")),
            live=_truthy(os.getenv("X_AUTOPOST_LIVE")),
            owner_id=os.getenv(
                "X_AUTOPOST_OWNER_ID", os.getenv("MASTER_USER_ID", "")
            ).strip(),
            topics=topics,
            timezone_name=timezone_name,
            window_start_hour=_bounded_int(
                os.getenv("X_AUTOPOST_WINDOW_START_HOUR"), 8, 0, 23
            ),
            window_end_hour=_bounded_int(
                os.getenv("X_AUTOPOST_WINDOW_END_HOUR"), 23, 0, 23
            ),
            daily_max=_bounded_int(
                os.getenv("X_AUTOPOST_DAILY_MAX"), HARD_DAILY_CAP, 1, HARD_DAILY_CAP
            ),
            check_interval_seconds=_bounded_int(
                os.getenv("X_AUTOPOST_CHECK_INTERVAL_SECONDS"), 60, 30, 3600
            ),
            startup_delay_seconds=_bounded_int(
                os.getenv("X_AUTOPOST_STARTUP_DELAY_SECONDS"), 20, 0, 600
            ),
        )

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def blockers(self, *, account_connected: bool) -> list[str]:
        blockers = []
        if not self.enabled:
            blockers.append("automation_disabled")
        if not self.live:
            blockers.append("dry_run_only")
        if not self.owner_id:
            blockers.append("owner_missing")
        if not self.topics:
            blockers.append("topics_missing")
        if self.live and not account_connected:
            blockers.append("x_account_not_connected")
        return blockers


def build_daily_schedule(
    local_day: date,
    config: XAutopostConfig,
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> list[datetime]:
    """Spread randomized slots across the active window without burst posting."""
    random_source = rng or random.SystemRandom()
    start = datetime.combine(
        local_day, time(config.window_start_hour), tzinfo=config.timezone
    )
    end = datetime.combine(
        local_day, time(config.window_end_hour), tzinfo=config.timezone
    )
    if end <= start:
        end += timedelta(days=1)
    span_seconds = max(1, int((end - start).total_seconds()))
    count = min(config.daily_max, max(1, span_seconds // (15 * 60)))
    segment = span_seconds / count
    schedule = []
    for index in range(count):
        # Choose from the middle 80% of each segment. This keeps slots random
        # while ensuring they remain distributed throughout the day.
        offset = segment * (index + random_source.uniform(0.1, 0.9))
        schedule.append((start + timedelta(seconds=offset)).astimezone(timezone.utc))
    return schedule


def validate_autopost_text(text: str, recent: list[str] | tuple[str, ...] = ()) -> str:
    """Reject content that is unsafe for unattended public posting."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip().strip('"')
    if not clean or len(clean) > 280:
        raise ValueError("Autopost text must be between 1 and 280 characters")
    if _URL.search(clean):
        raise ValueError("Autoposts cannot contain unreviewed links")
    if _MENTION.search(clean):
        raise ValueError("Autoposts cannot mention users")
    if _TAG.search(clean):
        raise ValueError("Autoposts cannot use hashtags or cashtags")
    if _RISKY_ADVICE.search(clean) or _CURRENT_EVENT.search(clean):
        raise ValueError(
            "Autopost contains advice or an unverified current-event claim"
        )
    for prior in recent[-200:]:
        if (
            SequenceMatcher(None, clean.casefold(), str(prior).casefold()).ratio()
            >= 0.76
        ):
            raise ValueError("Autopost is too similar to recent content")
    return clean


def _load_state(owner_id: str) -> dict[str, Any]:
    rows = list_personal_items(owner_id, _STATE_KIND)
    return dict(rows[-1]) if rows else {"id": _STATE_SOURCE_ID, "version": 1}


def _save_state(owner_id: str, state: dict[str, Any]) -> None:
    state = dict(state)
    state.setdefault("id", _STATE_SOURCE_ID)
    state["version"] = 1
    save_personal_item(owner_id, _STATE_KIND, state)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_autopost_prompt(topic: str, recent: list[str]) -> str:
    """Build a public-post prompt using Curie's complete active persona."""
    task = (
        "Write one original X post about this operator-approved topic: "
        f"{topic}. Maximum 240 characters. Curie must be recognizable through her "
        "warm scientific curiosity, precise observation, friendly candor, light French "
        "rhythm, and occasional understated wit. A short natural French expression may "
        "appear when it genuinely fits, but do not force one and do not use the same "
        "mannerism repeatedly. Write an evergreen observation or question, not generic "
        "brand content. Do not include news, live claims, financial, medical, or legal "
        "advice, calls to action, links, mentions, hashtags, cashtags, private facts, or "
        "claims of having senses or physical experiences. Do not repeat these recent posts:\n"
        + "\n".join(f"- {item}" for item in recent[-12:])
        + "\nReturn only the post text."
    )
    return apply_persona_contract(task, medium="public X post")


class XAutopostService:
    """Generate at most one due post per check under a persistent daily plan."""

    def __init__(self, config: XAutopostConfig | None = None):
        self.config = config or XAutopostConfig.from_env()
        self.running = False
        self.thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self.running or not self.config.enabled:
            return
        self.running = True
        self._stop_event.clear()
        self.thread = threading.Thread(
            target=self._run, name="curie-x-autopost", daemon=True
        )
        self.thread.start()
        logger.info(
            "X autopost service started live=%s daily_max=%d timezone=%s",
            self.config.live,
            self.config.daily_max,
            self.config.timezone_name,
        )

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self) -> None:
        if self._stop_event.wait(self.config.startup_delay_seconds):
            return
        while self.running:
            try:
                asyncio.run(self.check_once())
            except Exception as exc:
                logger.error("X autopost check failed: %s", exc, exc_info=True)
            if self._stop_event.wait(self.config.check_interval_seconds):
                return

    def _ensure_today(self, state: dict[str, Any], now: datetime) -> dict[str, Any]:
        local_day = now.astimezone(self.config.timezone).date().isoformat()
        if state.get("local_day") == local_day:
            return state
        schedule = build_daily_schedule(date.fromisoformat(local_day), self.config)
        return {
            "id": state.get("id", _STATE_SOURCE_ID),
            "version": 1,
            "local_day": local_day,
            "schedule": [
                {"due_at": value.isoformat(), "status": "pending"} for value in schedule
            ],
            "posted_count": 0,
            "halted_for_day": False,
            "recent_posts": list(state.get("recent_posts", []))[-200:],
        }

    async def _generate(self, state: dict[str, Any]) -> str:
        from llm.providers import ask_best_provider

        topic = random.SystemRandom().choice(self.config.topics)
        recent = [str(item.get("text", "")) for item in state.get("recent_posts", [])]
        prompt = build_autopost_prompt(topic, recent)
        response = await asyncio.to_thread(
            ask_best_provider, prompt, temperature=0.75, max_tokens=100
        )
        return validate_autopost_text(response, recent)

    async def check_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Process no more than one due slot; never catch up in a public burst."""
        now = now or datetime.now(timezone.utc)
        if not self.config.owner_id:
            return {"status": "blocked", "reason": "owner_missing"}

        from services.credential_vault import get_credential

        connected = bool(get_credential(self.config.owner_id, "x"))
        blockers = self.config.blockers(account_connected=connected)
        if blockers and blockers != ["dry_run_only"]:
            return {"status": "blocked", "reasons": blockers}

        state = self._ensure_today(_load_state(self.config.owner_id), now)
        if (
            state.get("halted_for_day")
            or int(state.get("posted_count", 0)) >= self.config.daily_max
        ):
            _save_state(self.config.owner_id, state)
            return {"status": "daily_complete"}

        pending = [
            (index, slot)
            for index, slot in enumerate(state.get("schedule", []))
            if slot.get("status") == "pending"
            and datetime.fromisoformat(str(slot["due_at"])) <= now
        ]
        if not pending:
            _save_state(self.config.owner_id, state)
            return {"status": "not_due"}

        # Mark all but the newest overdue slot as missed. A restart must never
        # release a backlog of public posts at once.
        for index, slot in pending[:-1]:
            state["schedule"][index] = {**slot, "status": "missed"}
        slot_index, slot = pending[-1]
        try:
            text = await self._generate(state)
            if self.config.live:
                from connectors.twitter import create_post

                payload = await create_post(self.config.owner_id, text)
                post = payload.get("data", {})
                post_id = str(post.get("id", ""))
                status = "posted"
            else:
                post_id = ""
                status = "previewed"
            state["schedule"][slot_index] = {
                **slot,
                "status": status,
                "processed_at": now.isoformat(),
                "content_hash": _content_hash(text),
                "post_id": post_id,
            }
            state["posted_count"] = int(state.get("posted_count", 0)) + 1
            state["recent_posts"] = [
                *list(state.get("recent_posts", []))[-199:],
                {"text": text, "created_at": now.isoformat(), "post_id": post_id},
            ]
            _save_state(self.config.owner_id, state)
            from memory.repositories import get_repositories

            get_repositories().audits.append(
                self.config.owner_id,
                "x_autopost",
                status,
                {
                    "content_hash": _content_hash(text),
                    "length": len(text),
                    "post_id": post_id,
                    "local_day": state["local_day"],
                },
            )
            return {"status": status, "post_id": post_id, "text": text}
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            state["schedule"][slot_index] = {
                **slot,
                "status": "failed",
                "processed_at": now.isoformat(),
                "http_status": code,
            }
            if code in {403, 429}:
                state["halted_for_day"] = True
            _save_state(self.config.owner_id, state)
            return {"status": "failed", "http_status": code}
        except Exception as exc:
            state["schedule"][slot_index] = {
                **slot,
                "status": "rejected",
                "processed_at": now.isoformat(),
                "reason": type(exc).__name__,
            }
            _save_state(self.config.owner_id, state)
            logger.warning("X autopost slot rejected: %s", exc)
            return {"status": "rejected", "reason": type(exc).__name__}


def status_snapshot(config: XAutopostConfig | None = None) -> dict[str, Any]:
    """Return a secret-free operator status for health checks and tests."""
    cfg = config or XAutopostConfig.from_env()
    connected = False
    if cfg.owner_id:
        try:
            from services.credential_vault import get_credential

            connected = bool(get_credential(cfg.owner_id, "x"))
        except Exception:
            connected = False
    return {
        "enabled": cfg.enabled,
        "live": cfg.live,
        "account_connected": connected,
        "topics_configured": bool(cfg.topics),
        "daily_max": cfg.daily_max,
        "hard_daily_cap": HARD_DAILY_CAP,
        "timezone": cfg.timezone_name,
        "blockers": cfg.blockers(account_connected=connected),
    }
