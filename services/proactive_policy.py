"""Persistent, user-controlled policy for unsolicited proactive messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from memory import UserManager

_COMMAND = re.compile(r"^/?proactive(?:\s+(?P<action>.*))?$", re.I)
_WHY = re.compile(r"\bwhy did you (?:send|message|suggest) (?:this|that)\b", re.I)
_DURATIONS = {"1h": 1, "8h": 8, "1d": 24, "1w": 168}
_KIND_WEIGHT = {
    "deadline": 1.0,
    "reminder": 0.95,
    "travel_disruption": 0.9,
    "commitment": 0.82,
    "device_health": 0.75,
    "tracked_goal": 0.7,
    "routine": 0.55,
    "check_in": 0.2,
}
_DEVICE_COMMAND_THEME = re.compile(
    r"\b(?:turn|switch|power)\s+(?:on|off)\b|\b(?:all|both)\s+(?:lights?|devices?)\b",
    re.I,
)
_UNSUPPORTED_PERSONAL_CLAIM = re.compile(
    r"\bi\s+(?:saw|noticed|heard|watched|felt|remembered)\b|"
    r"\b(?:the sky|your mood|your expression)\s+(?:looks?|seems?)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ProactiveEvidence:
    source: str
    reference: str
    confidence: float
    observed_at: str = ""

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "ProactiveEvidence":
        return cls(
            source=str(value.get("source") or "unknown")[:64],
            reference=str(value.get("reference") or "")[:160],
            confidence=max(0.0, min(float(value.get("confidence", 0)), 1.0)),
            observed_at=str(value.get("observed_at") or "")[:64],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "reference": self.reference,
            "confidence": self.confidence,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class ProactiveCandidate:
    kind: str
    topic: str
    reason: str
    confidence: float
    urgency: float = 0.0
    usefulness: float = 0.0
    priority: float = 0.5
    message: str = ""
    evidence: tuple[ProactiveEvidence, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProactiveCandidate":
        evidence = tuple(
            ProactiveEvidence.from_value(item)
            for item in value.get("evidence", ())
            if isinstance(item, Mapping)
        )
        return cls(
            kind=str(value.get("kind") or "routine")[:40],
            topic=str(value.get("topic") or "general")[:80],
            reason=str(value.get("reason") or "")[:180],
            confidence=max(0.0, min(float(value.get("confidence", 0)), 1.0)),
            urgency=max(0.0, min(float(value.get("urgency", 0)), 1.0)),
            usefulness=max(0.0, min(float(value.get("usefulness", 0)), 1.0)),
            priority=max(0.0, min(float(value.get("priority", 0.5)), 1.0)),
            message=str(value.get("message") or "")[:1000],
            evidence=evidence,
        )

    def rejection_reason(self) -> str | None:
        if not self.reason:
            return "missing_reason"
        if _DEVICE_COMMAND_THEME.search(f"{self.topic} {self.message}"):
            return "device_command_is_not_a_theme"
        if _UNSUPPORTED_PERSONAL_CLAIM.search(self.message) and not self.evidence:
            return "unsupported_personal_claim"
        if self.evidence and max(item.confidence for item in self.evidence) < 0.65:
            return "weak_evidence"
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "topic": self.topic,
            "reason": self.reason,
            "confidence": self.confidence,
            "urgency": self.urgency,
            "usefulness": self.usefulness,
            "priority": self.priority,
            "message": self.message,
            "evidence": [item.as_dict() for item in self.evidence],
        }


def rank_candidates(candidates: list[dict], profile: dict) -> list[dict]:
    """Rank grounded opportunities and attach a traceable score explanation."""
    excluded = {
        str(value).casefold() for value in profile.get("proactive_avoid_topics", [])
    }
    ranked = []
    for raw_candidate in candidates:
        candidate = ProactiveCandidate.from_mapping(raw_candidate)
        topic = candidate.topic.casefold()
        if any(item and item in topic for item in excluded):
            continue
        if candidate.rejection_reason() is not None:
            continue
        confidence = candidate.confidence
        if confidence < 0.65:
            continue
        urgency = candidate.urgency
        usefulness = candidate.usefulness
        priority = candidate.priority
        kind = candidate.kind
        score = (
            0.28 * urgency
            + 0.28 * usefulness
            + 0.24 * confidence
            + 0.12 * priority
            + 0.08 * _KIND_WEIGHT.get(kind, 0.4)
        )
        ranked.append(
            {
                **candidate.as_dict(),
                "score": round(score, 4),
                "ranking_reason": f"kind={kind}; urgency={urgency:.2f}; usefulness={usefulness:.2f}; confidence={confidence:.2f}; priority={priority:.2f}",
            }
        )
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def _parse_time(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def settings(profile: dict) -> dict:
    """Return the bounded, user-visible proactive settings."""
    quiet = profile.get("proactive_quiet_hours") or {"start": 22, "end": 8}
    return {
        "enabled": profile.get("proactive_messaging_enabled") is True,
        "timezone": profile.get("timezone") or "UTC",
        "quiet_hours": {
            "start": int(quiet.get("start", 22)) % 24,
            "end": int(quiet.get("end", 8)) % 24,
        },
        "daily_max": max(0, min(int(profile.get("proactive_daily_max", 1)), 10)),
        "weekly_max": max(0, min(int(profile.get("proactive_weekly_max", 7)), 50)),
        "topic_cooldown_hours": max(
            1, min(int(profile.get("proactive_topic_cooldown_hours", 72)), 720)
        ),
        "interval_hours": max(
            1.0, min(float(profile.get("proactive_interval_hours", 24)), 168.0)
        ),
        "style": str(profile.get("proactive_style", "balanced")),
        "snoozed_until": profile.get("proactive_snoozed_until"),
        "excluded_topics": list(profile.get("proactive_avoid_topics", []))[:20],
        "delivery_channels": list(profile.get("proactive_delivery_channels", []))[:10],
    }


def delivery_allowed(
    profile: dict, topic: str, now: datetime | None = None
) -> tuple[bool, str]:
    """Evaluate every delivery bound without mutating the profile."""
    now = now or datetime.now(timezone.utc)
    cfg = settings(profile)
    if not cfg["enabled"]:
        return False, "not_opted_in"
    if any(
        str(item).casefold() in str(topic).casefold()
        for item in cfg["excluded_topics"]
        if str(item).strip()
    ):
        return False, "excluded_topic"
    snooze = _parse_time(cfg["snoozed_until"])
    if snooze and now < snooze:
        return False, "snoozed"
    try:
        local_now = now.astimezone(ZoneInfo(cfg["timezone"]))
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return False, "invalid_timezone"
    start, end = cfg["quiet_hours"].values()
    quiet = start != end and (
        start <= local_now.hour < end
        if start < end
        else local_now.hour >= start or local_now.hour < end
    )
    if quiet:
        return False, "quiet_hours"
    sent = [
        dt
        for value in profile.get("proactive_sent_at", [])
        if (dt := _parse_time(value))
    ]
    if sum(dt >= now - timedelta(days=1) for dt in sent) >= cfg["daily_max"]:
        return False, "daily_limit"
    if sum(dt >= now - timedelta(days=7) for dt in sent) >= cfg["weekly_max"]:
        return False, "weekly_limit"
    last_topic = _parse_time(
        (profile.get("proactive_topic_last_sent") or {}).get(topic)
    )
    rejection_count = max(0, int(profile.get("proactive_rejection_count", 0)))
    # Silence is ambiguous and must not be treated as rejection. Only explicit
    # negative feedback lengthens a topic cooldown.
    multiplier = min(4, 1 + rejection_count)
    if last_topic and now - last_topic < timedelta(
        hours=cfg["topic_cooldown_hours"] * multiplier
    ):
        return False, "topic_cooldown"
    return True, "allowed"


def delivery_updates(
    profile: dict, topic: str, reason: str, now: datetime | None = None
) -> dict:
    """Build bounded persistence fields after a confirmed successful delivery."""
    now = now or datetime.now(timezone.utc)
    sent = [
        dt.isoformat()
        for value in profile.get("proactive_sent_at", [])
        if (dt := _parse_time(value)) and dt >= now - timedelta(days=7)
    ]
    topics = dict(profile.get("proactive_topic_last_sent") or {})
    topics[str(topic)[:80]] = now.isoformat()
    return {
        "proactive_sent_at": [*sent, now.isoformat()][-50:],
        "proactive_topic_last_sent": dict(list(topics.items())[-50:]),
        "proactive_last_topic": str(topic)[:80],
        "proactive_last_reason": str(reason)[:180],
        "proactive_awaiting_response": True,
    }


def mark_user_response(internal_id: str, profile: dict) -> None:
    if profile.get("proactive_awaiting_response"):
        UserManager.update_user_profile(
            internal_id, {"proactive_awaiting_response": False}
        )


def handle_proactive_command(internal_id: str, text: str) -> str | None:
    """Handle opt-in, settings, snooze, disable, and privacy-safe why controls."""
    match = _COMMAND.match(text.strip())
    profile = UserManager.get_user_profile(internal_id) or {}
    if not match:
        if _WHY.search(text):
            reason = (
                profile.get("proactive_last_reason")
                or "No proactive message reason is available."
            )
            return (
                f"Why: {reason} You can use /proactive snooze 1d or /proactive disable."
            )
        return None
    action = (match.group("action") or "settings").strip().casefold()
    if action in {"enable", "on"}:
        UserManager.update_user_profile(
            internal_id, {"proactive_messaging_enabled": True}
        )
        return "Proactive messages are enabled. Use /proactive to review the delivery limits."
    if action in {"disable", "off"}:
        UserManager.update_user_profile(
            internal_id, {"proactive_messaging_enabled": False}
        )
        return "Proactive messages are disabled."
    if action.startswith("snooze "):
        duration = action.split(maxsplit=1)[1]
        if duration not in _DURATIONS:
            return "Choose a snooze duration: 1h, 8h, 1d, or 1w."
        until = datetime.now(timezone.utc) + timedelta(hours=_DURATIONS[duration])
        UserManager.update_user_profile(
            internal_id, {"proactive_snoozed_until": until.isoformat()}
        )
        return f"Proactive messages are snoozed until {until.isoformat()}."
    if action.startswith("exclude "):
        topic = action.split(maxsplit=1)[1].strip()[:80]
        topics = list(
            dict.fromkeys([*profile.get("proactive_avoid_topics", []), topic])
        )[-20:]
        UserManager.update_user_profile(internal_id, {"proactive_avoid_topics": topics})
        return f"I won’t send proactive messages about `{topic}`."
    if action.startswith("allow-topic "):
        topic = action.split(maxsplit=1)[1].strip().casefold()
        topics = [
            item
            for item in profile.get("proactive_avoid_topics", [])
            if str(item).casefold() != topic
        ]
        UserManager.update_user_profile(internal_id, {"proactive_avoid_topics": topics})
        return f"Proactive messages about `{topic}` are allowed again."
    if action.startswith("channel "):
        channel = action.split(maxsplit=1)[1].strip().casefold()
        if channel not in {"telegram", "discord", "whatsapp", "slack", "api"}:
            return "Choose telegram, discord, whatsapp, slack, or api."
        UserManager.update_user_profile(
            internal_id, {"proactive_delivery_channels": [channel]}
        )
        return f"Proactive messages will prefer {channel}."
    if action.startswith("mode "):
        mode = action.split(maxsplit=1)[1].strip().casefold()
        presets = {
            "quiet": {
                "proactive_style": "quiet",
                "proactive_interval_hours": 24,
                "proactive_daily_max": 1,
                "proactive_weekly_max": 7,
                "proactive_topic_cooldown_hours": 72,
                "proactive_generation_cooldown_hours": 6,
            },
            "balanced": {
                "proactive_style": "balanced",
                "proactive_interval_hours": 8,
                "proactive_daily_max": 2,
                "proactive_weekly_max": 12,
                "proactive_topic_cooldown_hours": 36,
                "proactive_generation_cooldown_hours": 4,
            },
            "companion": {
                "proactive_style": "companion",
                "proactive_interval_hours": 3,
                "proactive_daily_max": 4,
                "proactive_weekly_max": 24,
                "proactive_topic_cooldown_hours": 12,
                "proactive_generation_cooldown_hours": 2,
            },
        }
        if mode not in presets:
            return "Choose proactive mode: quiet, balanced, or companion."
        UserManager.update_user_profile(
            internal_id,
            {
                **presets[mode],
                "proactive_messaging_enabled": True,
                "proactive_ignored_count": 0,
                "proactive_awaiting_response": False,
            },
        )
        return (
            f"Proactive mode is now {mode}: up to {presets[mode]['proactive_daily_max']} "
            f"messages per day, normally at least {presets[mode]['proactive_interval_hours']} "
            "hours apart, with quiet hours still respected."
        )
    if action in {"why", "why did you send this?", "settings", "status"}:
        if action.startswith("why"):
            reason = (
                profile.get("proactive_last_reason")
                or "No proactive message reason is available."
            )
            return f"Why: {reason}"
        cfg = settings(profile)
        quiet = cfg["quiet_hours"]
        return (
            f"Proactive: {'enabled' if cfg['enabled'] else 'disabled'}; timezone {cfg['timezone']}; "
            f"mode {cfg['style']}; quiet hours {quiet['start']:02d}:00–{quiet['end']:02d}:00; "
            f"cadence {cfg['interval_hours']:g}h; limits {cfg['daily_max']}/day, "
            f"{cfg['weekly_max']}/week; topic cooldown {cfg['topic_cooldown_hours']}h; "
            f"snoozed until {cfg['snoozed_until'] or 'not snoozed'}. Excluded topics: "
            f"{', '.join(cfg['excluded_topics']) or 'none'}; channels: "
            f"{', '.join(cfg['delivery_channels']) or 'identity-linked default'}."
        )
    return "Use /proactive, enable, disable, mode quiet|balanced|companion, snooze 1d, exclude <topic>, channel <name>, or why."
