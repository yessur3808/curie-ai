# services/proactive_messaging.py
"""
Proactive Messaging Service - Randomly checks in with users like a caring friend.

This service:
1. Schedules random check-ins throughout the day/week
2. Generates contextual, caring messages
3. Respects user preferences and busy status
4. Integrates with all connectors (Telegram, Discord, WhatsApp, API)
"""

import asyncio
import logging
import inspect
import os
import random
import re
import threading
from datetime import datetime, timezone
from typing import Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from memory import UserManager
from memory.session_store import get_session_manager
from services.proactive_policy import delivery_allowed, delivery_updates

logger = logging.getLogger(__name__)

_SENSORY_CLAIM = re.compile(
    r"\b(?:i\s+(?:saw|noticed|watched|heard|felt)|today(?:'s)?\s+(?:sky|clouds?)|"
    r"sky observation|cloud pattern|satellite imagery)\b",
    re.I,
)
_WORD = re.compile(r"[a-z0-9]{3,}", re.I)
_STOPWORDS = frozenset(
    {
        "about",
        "again",
        "another",
        "could",
        "from",
        "have",
        "help",
        "like",
        "make",
        "prepare",
        "share",
        "that",
        "their",
        "them",
        "this",
        "today",
        "want",
        "with",
        "would",
        "your",
    }
)


def _message_topic(message: str) -> str:
    words = [word.casefold() for word in _WORD.findall(message) if word.casefold() not in _STOPWORDS]
    return " ".join(words[:3]) or "general check-in"

# ---------------------------------------------------------------------------
# Platform column mappings — single source of truth used by both the reminder
# delivery path and the proactive messaging eligibility check.
# ---------------------------------------------------------------------------

# Maps platform name → PostgreSQL column that holds the user's external ID.
_PLATFORM_TO_COL: dict = {
    "telegram": "telegram_id",
    "discord": "discord_id",
    "whatsapp": "whatsapp_id",
    "api": "api_id",
}

# Default order in which platforms are tried when no user preference is set.
_DEFAULT_PLATFORM_PRIORITY: list = ["telegram", "discord", "whatsapp", "api"]

# Pre-built fully-static SQL queries per platform (no dynamic SQL construction).
_PLATFORM_QUERIES: dict = {
    platform: f"SELECT {col} FROM users WHERE internal_id = %s"
    for platform, col in _PLATFORM_TO_COL.items()
}


def _hour_in_quiet_window(hour: int, start: int, end: int) -> bool:
    """Return whether *hour* is inside a possibly overnight quiet window."""
    hour, start, end = hour % 24, start % 24, end % 24
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _resolve_contact_channels(facts: dict) -> dict:
    """Extract contact-channel preferences from a user *facts* dict.

    Returns a normalised dict with three keys:

    ``platform_priority``
        Ordered list of platforms to try, most-preferred first.
        Only platforms present in :data:`_PLATFORM_TO_COL` are kept.

    ``blocked_platforms``
        ``frozenset`` of platform names the user does not want to be
        contacted on.

    ``account_priority``
        ``dict`` mapping platform name → ordered list of preferred
        account IDs.  An empty list means "use all registered IDs in
        database order".
    """
    cc = facts.get("contact_channels", {})
    raw_priority = cc.get("platform_priority", _DEFAULT_PLATFORM_PRIORITY)
    blocked = frozenset(cc.get("blocked_platforms", []))
    # Keep only known platforms and exclude blocked ones.
    priority = [p for p in raw_priority if p in _PLATFORM_TO_COL and p not in blocked]
    return {
        "platform_priority": priority,
        "blocked_platforms": blocked,
        "account_priority": dict(cc.get("account_priority", {})),
    }


class ProactiveMessagingService:
    """
    Service that sends proactive, caring check-in messages to users.
    """

    # Maximum entries to keep in memory (prevents unbounded growth)
    # Memory estimate: ~100 bytes per entry = ~100KB for 1000 entries
    # This limit should be sufficient for most deployments while preventing memory issues
    MAX_CONTACT_HISTORY = 1000

    def __init__(self, workflow, connectors: Dict = None):
        """
        Initialize the proactive messaging service.

        Args:
            workflow: The active ChatWorkflow used by all connectors
            connectors: Dict mapping platform names to connector instances
        """
        self.workflow = workflow
        self.connectors = connectors or {}
        self.running = False
        self.thread = None
        self.check_interval = max(
            60, int(os.getenv("PROACTIVE_CHECK_INTERVAL", "3600"))
        )
        self.startup_delay = max(0, int(os.getenv("PROACTIVE_STARTUP_DELAY", "10")))
        self.message_probability = min(
            1.0, max(0.0, float(os.getenv("PROACTIVE_MESSAGE_PROBABILITY", "1.0")))
        )
        self._stop_event = threading.Event()

        # Tracking when we last messaged each user
        # Note: In production, this should be persisted to database
        # For now, using in-memory cache with size limit
        self.last_contact = {}
        self.last_contact_lock = threading.Lock()  # Thread-safe access
        self._generation_reasons: dict[str, str] = {}

        # Cron job runner – started/stopped alongside this service
        self._cron_runner = None

        logger.info("ProactiveMessagingService initialized")

    def start(self):
        """Start the proactive messaging service in a background thread."""
        if self.running:
            logger.warning("ProactiveMessagingService already running")
            return

        self.running = True
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run_service, daemon=True)
        self.thread.start()
        logger.info("✅ ProactiveMessagingService started")

        # Start the cron runner alongside
        try:
            from services.cron_runner import CronRunner  # noqa: PLC0415

            self._cron_runner = CronRunner(
                workflow=self.workflow, connectors=self.connectors
            )
            self._cron_runner.start()
            logger.info("✅ CronRunner started")
        except Exception as e:
            logger.warning("CronRunner could not start: %s", e)

    def stop(self):
        """Stop the proactive messaging service."""
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

        if self._cron_runner is not None:
            try:
                self._cron_runner.stop()
            except Exception as e:
                logger.warning("CronRunner stop error: %s", e)
            self._cron_runner = None

        logger.info("ProactiveMessagingService stopped")

    def _run_service(self):
        """Main service loop that runs in background thread."""
        logger.info("ProactiveMessagingService loop started")

        if self._stop_event.wait(self.startup_delay):
            return

        while self.running:
            try:
                # Run async check in sync thread
                asyncio.run(self._check_and_send_messages())
            except Exception as e:
                logger.error(f"Error in proactive messaging loop: {e}", exc_info=True)

            # Wait interruptibly so shutdown never blocks for a full interval.
            if self._stop_event.wait(self.check_interval):
                break

    async def _check_and_send_messages(self):
        """Check all users and send proactive messages / due reminders if appropriate."""
        try:
            # Clean up old contact history periodically
            self._cleanup_old_contacts()

            # Deliver any due reminders first (time-sensitive)
            await self._deliver_due_reminders()

            # Get all users who have the proactive_messaging preference enabled
            # For now, we'll check users from recent conversations
            users = self._get_eligible_users()

            for user_info in users:
                try:
                    await self._maybe_send_proactive_message(user_info)
                except Exception as e:
                    logger.error(
                        f"Error sending proactive message to user {user_info.get('internal_id')}: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(
                f"Error checking users for proactive messaging: {e}", exc_info=True
            )

    async def _deliver_due_reminders(self):
        """Check MongoDB for due reminders and deliver them to users."""
        try:
            from datetime import timezone as tz_module
            from memory.repositories import get_repositories

            now = datetime.now(tz_module.utc)
            repositories = get_repositories()
            due_docs = repositories.reminders.due(now)

            if not due_docs:
                return

            logger.info("Found %d due reminder(s) to deliver", len(due_docs))

            for doc in due_docs:
                try:
                    internal_id = doc.get("internal_id")
                    platform = doc.get("platform", "unknown")
                    message_text = doc.get("message", "your reminder")
                    # Apply platform-appropriate formatting (WhatsApp needs plain text)
                    from utils.formatting import (
                        format_for_platform,
                        escape_markdown,
                    )  # noqa: PLC0415

                    escaped_message_text = escape_markdown(str(message_text))
                    reminder_msg = format_for_platform(
                        f"⏰ Reminder: **{escaped_message_text}**", platform
                    )

                    # Fetch the user's contact-channel preferences so we can
                    # respect their platform priority and blocked-platform list.
                    try:
                        user_facts = UserManager.get_user_profile(internal_id) or {}
                    except Exception:
                        user_facts = {}
                    cc = _resolve_contact_channels(user_facts)

                    if platform in cc["blocked_platforms"]:
                        # The user has opted out of this platform — try their
                        # next preferred platform instead.
                        logger.info(
                            "Reminder %s: platform=%r is blocked for user %s; trying fallback platforms",
                            doc["_id"],
                            platform,
                            internal_id,
                        )
                        fallback_platforms = [
                            p
                            for p in cc["platform_priority"]
                            if p != platform and p not in cc["blocked_platforms"]
                        ]
                    else:
                        # Honour the reminder's platform; ensure it comes first.
                        fallback_platforms = [platform] + [
                            p
                            for p in cc["platform_priority"]
                            if p != platform and p not in cc["blocked_platforms"]
                        ]

                    delivered = False
                    for attempt_platform in fallback_platforms:
                        connector = self.connectors.get(attempt_platform)
                        if not connector:
                            continue

                        platform_col = _PLATFORM_TO_COL.get(attempt_platform)
                        if platform_col is None:
                            continue

                        try:
                            external_id = repositories.identities.get_external_id(
                                str(internal_id), attempt_platform
                            )
                            external_user_ids = [external_id] if external_id else []
                        except Exception as db_err:
                            logger.warning(
                                "Could not look up external_user_id for reminder: %s",
                                db_err,
                            )

                        # Order account IDs by the user's per-platform preference.
                        preferred_ids = cc["account_priority"].get(attempt_platform, [])
                        if preferred_ids:
                            ordered_ids = preferred_ids + [
                                i for i in external_user_ids if i not in preferred_ids
                            ]
                        else:
                            ordered_ids = external_user_ids

                        for ext_uid in ordered_ids:
                            if await self._send_via_connector(
                                connector, ext_uid, reminder_msg
                            ):
                                delivered = True
                                break  # stop after first successful account
                            else:
                                logger.warning(
                                    "Delivery failed for reminder %s to uid=%s (internal_id=%s, platform=%s)",
                                    doc["_id"],
                                    ext_uid,
                                    internal_id,
                                    attempt_platform,
                                )

                        if delivered:
                            break  # stop after first successful platform

                    if not delivered and not fallback_platforms:
                        logger.warning(
                            "No connector available for platform=%s — reminder not delivered (internal_id=%s)",
                            platform,
                            internal_id,
                        )

                    # Only mark as fired after a confirmed successful delivery so
                    # the reminder is retried on the next loop cycle if delivery
                    # failed.  To prevent an infinite retry loop for permanently
                    # undeliverable reminders (e.g. user removed), we also track
                    # attempt_count and give up after a configurable maximum.
                    _MAX_REMINDER_ATTEMPTS = int(
                        os.getenv("REMINDER_MAX_ATTEMPTS", "5")
                    )
                    attempt_count = doc.get("attempt_count", 0)
                    if delivered:
                        repositories.reminders.mark_fired(doc["_id"])
                        logger.info(
                            "Reminder %s marked as fired (internal_id=%s)",
                            doc["_id"],
                            internal_id,
                        )
                    elif attempt_count + 1 >= _MAX_REMINDER_ATTEMPTS:
                        repositories.reminders.mark_fired(doc["_id"], failed=True)
                        logger.warning(
                            "Reminder %s abandoned after %d failed attempt(s) — marked fired/failed",
                            doc["_id"],
                            attempt_count + 1,
                        )
                    else:
                        repositories.reminders.record_attempt(
                            doc["_id"], datetime.now(tz_module.utc)
                        )
                        logger.debug(
                            "Reminder %s delivery attempt %d recorded — will retry next cycle",
                            doc["_id"],
                            attempt_count + 1,
                        )

                except Exception as rem_err:
                    logger.error(
                        "Error delivering reminder %s: %s", doc.get("_id"), rem_err
                    )

        except Exception as exc:
            logger.debug("_deliver_due_reminders skipped (non-critical): %s", exc)

    def _get_eligible_users(self):
        """
        Get list of users eligible for proactive messaging.
        Returns list of dicts with user info.

        Queries MongoDB for users with proactive_messaging_enabled=true
        and joins with PostgreSQL to get platform-specific IDs.

        Returns:
            List[Dict]: Each dict contains:
                - internal_id: UUID string
                - platform: 'telegram', 'discord', 'whatsapp', or 'api'
                - external_user_id: Platform-specific user ID
                - proactive_interval_hours: User's preferred interval (default: 24)
                - timezone: User's timezone (default: 'UTC')
                - busy: User's busy status (default: False)
        """
        from memory.repositories import get_repositories

        try:
            eligible = []
            seen = set()
            for user in get_repositories().profiles.list_with_identities():
                facts = user.get("facts", {})
                key = (str(user.get("internal_id")), user.get("platform"))
                if key in seen or not facts.get("proactive_messaging_enabled", False):
                    continue
                if (
                    facts.get("busy", False)
                    or user.get("platform") not in self.connectors
                ):
                    continue
                channels = _resolve_contact_channels(facts)
                if user.get("platform") not in channels["platform_priority"]:
                    continue
                delivery_channels = set(facts.get("proactive_delivery_channels", []))
                if delivery_channels and user.get("platform") not in delivery_channels:
                    continue
                seen.add(key)
                eligible.append(
                    {
                        "internal_id": user["internal_id"],
                        "platform": user["platform"],
                        "external_user_id": user["external_user_id"],
                        "proactive_interval_hours": facts.get(
                            "proactive_interval_hours", 24
                        ),
                        "timezone": facts.get("timezone") or "UTC",
                        "busy": False,
                    }
                )
            logger.info(
                "Found %d users eligible for proactive messaging via %s repositories",
                len(eligible),
                get_repositories().backend,
            )
            return eligible
        except Exception as exc:
            logger.error("Error querying eligible users: %s", exc, exc_info=True)
            return []

    def _cleanup_old_contacts(self):
        """
        Clean up old entries from last_contact cache to prevent memory growth.
        Keeps only the most recent MAX_CONTACT_HISTORY entries.
        """
        with self.last_contact_lock:
            if len(self.last_contact) > self.MAX_CONTACT_HISTORY:
                # Sort by timestamp and keep only recent entries
                sorted_contacts = sorted(
                    self.last_contact.items(), key=lambda x: x[1], reverse=True
                )
                self.last_contact = dict(sorted_contacts[: self.MAX_CONTACT_HISTORY])
                logger.info(
                    f"Cleaned up contact history, kept {self.MAX_CONTACT_HISTORY} most recent entries"
                )

    async def _maybe_send_proactive_message(self, user_info: Dict):
        """
        Decide if we should send a proactive message to this user.

        Args:
            user_info: Dict with 'internal_id', 'platform', 'external_user_id', etc.
        """
        internal_id = user_info.get("internal_id")
        if not internal_id:
            return

        # Check user preferences
        user_profile = UserManager.get_user_profile(internal_id) or {}

        # Skip if user has disabled proactive messaging
        if not user_profile.get("proactive_messaging_enabled", False):
            return

        # Skip if user is marked as busy
        if user_profile.get("busy", False):
            logger.debug(f"User {internal_id} is busy, skipping proactive message")
            return

        # Get last conversation time (thread-safe)
        with self.last_contact_lock:
            last_contact_time = self.last_contact.get(internal_id)
        now = datetime.now(timezone.utc)  # Use timezone-aware datetime

        # A previous unsolicited message with no intervening response is an
        # ignored signal. Persist it so future topic cooldowns lengthen.
        if user_profile.get("proactive_awaiting_response"):
            ignored = max(0, int(user_profile.get("proactive_ignored_count", 0))) + 1
            UserManager.update_user_profile(
                internal_id,
                {"proactive_ignored_count": min(ignored, 10), "proactive_awaiting_response": False},
            )
            user_profile = {**user_profile, "proactive_ignored_count": min(ignored, 10), "proactive_awaiting_response": False}

        allowed, _reason = delivery_allowed(user_profile, "", now)
        if not allowed:
            return

        # Quiet hours and a persisted daily cap prevent Curie from becoming
        # intrusive even when the service restarts.
        try:
            timezone_name = user_profile.get("timezone") or "UTC"
            local_now = now.astimezone(ZoneInfo(timezone_name))
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            local_now = now
        quiet = user_profile.get("proactive_quiet_hours", {"start": 22, "end": 8})
        quiet_start = int(quiet.get("start", 22)) % 24
        quiet_end = int(quiet.get("end", 8)) % 24
        in_quiet_hours = _hour_in_quiet_window(local_now.hour, quiet_start, quiet_end)
        if in_quiet_hours:
            return

        today = local_now.date().isoformat()
        if user_profile.get("proactive_count_date") == today:
            if int(user_profile.get("proactive_count_today", 0)) >= int(
                user_profile.get("proactive_daily_max", 2)
            ):
                return

        for persisted_key in ("last_proactive_at", "last_user_interaction_at"):
            persisted_last = user_profile.get(persisted_key)
            if isinstance(persisted_last, str):
                try:
                    persisted_last = datetime.fromisoformat(
                        persisted_last.replace("Z", "+00:00")
                    )
                except ValueError:
                    persisted_last = None
            if isinstance(persisted_last, datetime):
                if persisted_last.tzinfo is None:
                    persisted_last = persisted_last.replace(tzinfo=timezone.utc)
                if last_contact_time is None or persisted_last > last_contact_time:
                    last_contact_time = persisted_last

        # Get user's preferred check-in interval (in hours)
        min_interval_hours = user_profile.get("proactive_interval_hours", 24)
        try:
            from memory.adaptation import get_adaptation_state

            adaptation = get_adaptation_state(str(internal_id))
            if adaptation["enabled"] and any(
                item.get("setting") == "notification_cadence_hours"
                for item in adaptation["history"]
            ):
                min_interval_hours = adaptation["preferences"][
                    "notification_cadence_hours"
                ]
        except Exception:
            pass
        negative_signals = max(0, int(user_profile.get("proactive_rejection_count", 0))) + max(
            0, int(user_profile.get("proactive_ignored_count", 0)) // 2
        )
        min_interval_hours = float(min_interval_hours) * min(4, 1 + negative_signals)

        # Check if enough time has passed
        if last_contact_time:
            hours_since_contact = (now - last_contact_time).total_seconds() / 3600
            if hours_since_contact < min_interval_hours:
                logger.debug(
                    f"Too soon to contact user {internal_id} ({hours_since_contact:.1f}h < {min_interval_hours}h)"
                )
                return

        # Randomly decide whether to send (using class constant)
        if random.random() > self.message_probability:
            logger.debug(
                f"Random check skipped proactive message for user {internal_id}"
            )
            return

        platform = user_info.get("platform")
        external_user_id = user_info.get("external_user_id")

        # Generate and send message
        message = await self._generate_proactive_message(
            internal_id, platform or "legacy"
        )
        topic = _message_topic(message)
        allowed, _reason = delivery_allowed(user_profile, topic, now)
        if not allowed:
            return

        # Send via appropriate connector

        if platform and external_user_id and platform in self.connectors:
            connector = self.connectors[platform]
            success = await self._send_via_connector(
                connector, external_user_id, message
            )

            if success:
                # Update last contact time (thread-safe)
                with self.last_contact_lock:
                    self.last_contact[internal_id] = now
                previous_count = (
                    int(user_profile.get("proactive_count_today", 0))
                    if user_profile.get("proactive_count_date") == today
                    else 0
                )
                safe_reason = self._generation_reasons.pop(
                    str(internal_id),
                    "You opted in and the configured check-in interval elapsed.",
                )
                UserManager.update_user_profile(
                    internal_id,
                    {
                        "last_proactive_at": now,
                        "proactive_count_date": today,
                        "proactive_count_today": previous_count + 1,
                        **delivery_updates(user_profile, topic, safe_reason, now),
                    },
                )
                # Store it in the same platform session used by normal chat so a
                # reply such as "why did you say that?" has the right context.
                get_session_manager().add_message(
                    platform, internal_id, "assistant", message
                )
                logger.info(
                    f"✅ Sent proactive message to user {internal_id} on {platform}"
                )
            else:
                logger.warning(
                    f"Failed to send proactive message to user {internal_id} on {platform}"
                )

    async def _generate_proactive_message(
        self, internal_id: str, platform: str = "legacy"
    ) -> str:
        """
        Generate a natural, caring check-in message.

        Args:
            internal_id: User's internal ID

        Returns:
            Generated message text
        """
        # Load user profile and recent history
        user_profile = UserManager.get_user_profile(internal_id) or {}
        history_rows = get_session_manager().get_history(platform, internal_id)
        history = [(row["role"], row["content"]) for row in history_rows[-10:]]
        candidates = []

        try:
            from services.personal_ops import daily_briefing_candidate

            briefing = daily_briefing_candidate(str(internal_id), user_profile)
            if briefing:
                candidates.append({**briefing, "kind": "deadline", "confidence": 1.0,
                                   "urgency": .8, "usefulness": .95, "priority": .9})
        except Exception as exc:
            logger.debug("Personal agenda briefing unavailable: %s", exc)

        # Prefer a grounded, permission-seeking helpful suggestion when the
        # neural predictor has enough evidence. It can propose but never act.
        if user_profile.get("proactive_predictions_enabled", True):
            try:
                from memory.adaptive import generate_helpful_prediction

                prediction = await asyncio.to_thread(
                    generate_helpful_prediction,
                    internal_id,
                    user_profile,
                    history,
                )
                if prediction and self._prediction_is_grounded(
                    prediction, user_profile, history
                ):
                    candidates.append({
                        "message": str(prediction["suggestion"]).strip(),
                        "reason": str(prediction["reason"]), "topic": _message_topic(str(prediction["suggestion"])),
                        "kind": "routine", "confidence": prediction.get("confidence", 0),
                        "urgency": .25, "usefulness": .7, "priority": .6,
                    })
            except Exception as exc:
                logger.debug("Proactive prediction unavailable: %s", exc)

        candidates.append({"message": "Salut, how’s your day going?", "reason":
                           "You opted in and the configured check-in interval elapsed.",
                           "topic": "general check-in", "kind": "check_in", "confidence": 1.0,
                           "urgency": 0, "usefulness": .2, "priority": .2})
        from services.proactive_policy import rank_candidates

        selected = rank_candidates(candidates, user_profile)[0]
        self._generation_reasons[str(internal_id)] = str(selected["reason"])[:180]
        logger.info("Selected proactive candidate for %s: %s", internal_id, selected["ranking_reason"])
        return str(selected["message"])

    @staticmethod
    def _prediction_is_grounded(
        prediction: dict, profile: dict, history: list[tuple[str, str]]
    ) -> bool:
        suggestion = str(prediction.get("suggestion", "")).strip()
        reason = str(prediction.get("reason", "")).strip()
        if (
            not suggestion.endswith("?")
            or not reason
            or _SENSORY_CLAIM.search(suggestion)
        ):
            return False
        try:
            from memory.adaptive import is_safe_proposed_action

            if not is_safe_proposed_action(suggestion):
                return False
        except Exception:
            return False
        avoided = {
            str(topic).casefold() for topic in profile.get("proactive_avoid_topics", [])
        }
        candidate_text = f"{suggestion} {reason}".casefold()
        if any(topic and topic in candidate_text for topic in avoided):
            return False
        evidence = " ".join(
            str(message) for role, message in history if role == "user"
        ).casefold()
        evidence += (
            " "
            + " ".join(
                str(value)
                for key, value in profile.items()
                if key in {"interests", "projects", "routines", "reminders_preference"}
            ).casefold()
        )
        evidence_words = set(_WORD.findall(evidence)) - _STOPWORDS
        reason_words = set(_WORD.findall(reason.casefold())) - _STOPWORDS
        shared = evidence_words & reason_words
        if not shared:
            return False
        # The predictor must identify at least two observations, or point to an
        # explicit recurring routine. One coincidental keyword is insufficient.
        evidence_count = int(prediction.get("evidence_count", 0) or 0)
        recurring = bool(re.search(r"\b(?:every|daily|weekly|usually|routine|often)\b", evidence, re.I))
        matching_messages = sum(
            bool(set(_WORD.findall(str(message).casefold())) & reason_words)
            for role, message in history
            if role == "user"
        )
        return evidence_count >= 2 and (matching_messages >= 2 or recurring)

    async def _send_via_connector(
        self, connector, external_user_id: str, message: str
    ) -> bool:
        """
        Send message via the appropriate connector.

        Args:
            connector: The connector instance
            external_user_id: Platform-specific user ID
            message: Message to send

        Returns:
            True if successful, False otherwise
        """
        try:
            # Different connectors may have different outbound message interfaces.
            # We support a small set of duck-typed options here so proactive sends
            # can work even if a connector doesn't expose `send_message` directly.

            async def _call_maybe_async(func, *args, **kwargs):
                """Call `func` which may be sync or async, returning after it completes."""
                # If it's declared as a coroutine function, call and await it.
                if inspect.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                # If calling it returns a coroutine, await that.
                result = func(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    return await result
                # Otherwise, offload the sync function to a thread.
                return await asyncio.to_thread(func, *args, **kwargs)

            # 1. Preferred interface: `send_message(external_user_id, message)`
            if hasattr(connector, "send_message"):
                result = await _call_maybe_async(
                    connector.send_message, external_user_id, message
                )
                return result is not False

            # 2. Common generic interfaces on some connectors: `send` or `send_text`
            if hasattr(connector, "send"):
                result = await _call_maybe_async(
                    connector.send, external_user_id, message
                )
                return result is not False

            if hasattr(connector, "send_text"):
                result = await _call_maybe_async(
                    connector.send_text, external_user_id, message
                )
                return result is not False

            # 3. Fallback: treat the connector itself as a callable sender.
            if callable(connector):
                result = await _call_maybe_async(connector, external_user_id, message)
                return result is not False

            # If we reach here, we don't know how to send via this connector.
            logger.warning(
                "Connector %s does not implement a supported outbound messaging interface "
                "(expected one of: send_message, send, send_text, or a callable).",
                type(connector).__name__,
            )
            return False
        except Exception as e:
            logger.error(f"Error sending message via connector: {e}", exc_info=True)
            return False
