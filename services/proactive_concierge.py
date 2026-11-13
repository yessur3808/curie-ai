"""Grounded proactive offers that make Curie useful between conversations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Iterable

from services.proactive_policy import delivery_allowed, settings

_ACKNOWLEDGEMENT = re.compile(
    r"^(?:thanks|thank you|cheers|ok(?:ay)?|cool|perfect|sounds good)[.! ]*$", re.I
)
_OPPORTUNITIES = (
    (
        "project next steps",
        "help",
        "practical_help",
        re.compile(
            r"\b(?:i(?:'m| am) working on|i need to (?:build|make|finish)|"
            r"i want to (?:build|make)|my (?:project|build))\b",
            re.I,
        ),
        "Want me to turn that project into the next three concrete steps?",
        0.28,
        0.82,
    ),
    (
        "plan checklist",
        "help",
        "practical_help",
        re.compile(
            r"\b(?:i(?:'m| am) planning|i need to plan|upcoming trip|"
            r"i have to organize|i need to organize)\b",
            re.I,
        ),
        "Want me to turn that plan into a compact checklist, with the awkward bits handled first?",
        0.3,
        0.84,
    ),
    (
        "drafting help",
        "assist",
        "assistance",
        re.compile(
            r"\b(?:i need to (?:write|reply|email|message)|i have to (?:write|reply)|"
            r"draft (?:an? )?(?:email|message|reply))\b",
            re.I,
        ),
        "Want me to draft a clean first version, so you only have to edit it?",
        0.22,
        0.8,
    ),
    (
        "decision support",
        "assist",
        "assistance",
        re.compile(
            r"\b(?:i need to (?:decide|choose|compare)|trying to (?:decide|choose)|"
            r"can(?:'t|not) decide)\b",
            re.I,
        ),
        "Want me to make a quick options table for that decision, including the tradeoffs?",
        0.24,
        0.82,
    ),
    (
        "task triage",
        "ease",
        "life_easier",
        re.compile(
            r"\b(?:too many things|so much to do|overwhelmed|messy list|"
            r"long (?:task|to-do|todo) list)\b",
            re.I,
        ),
        "Send me the messy list. I can sort it into now, later, and safely ignore.",
        0.35,
        0.88,
    ),
)

_EASE_OFFERS = (
    (
        "tedious task",
        "If something tedious is sitting on your list, hand me the rough version. I can turn it into a plan, draft, reminder, or checklist.",
    ),
    (
        "small friction",
        "Anything mildly annoying you have been postponing? Give me the rough details and I’ll help make it smaller.",
    ),
    (
        "organize the mess",
        "You can drop an unorganized thought dump on me whenever you like. I’ll turn it into something usable.",
    ),
)

_FUN_OFFERS = (
    (
        "tiny mystery",
        "Tiny diversion, if you want one: give me three random words and I’ll turn them into a miniature mystery.",
    ),
    (
        "brain break",
        "Want a sixty-second brain break? I can give you an original riddle, a strange hypothetical, or a tiny creative challenge.",
    ),
    (
        "overengineered invention",
        "If you fancy a little mischief, name any ordinary object and I’ll invent its wildly overengineered upgrade.",
    ),
)


def _available(profile: dict, topic: str, now: datetime) -> bool:
    allowed, _reason = delivery_allowed(profile, topic, now)
    return allowed


def _category_recently_sent(
    profile: dict,
    offers: tuple[tuple[str, str], ...],
    now: datetime,
) -> bool:
    """Keep generic ease/fun prompts occasional even when the daily cap is high."""
    sent = profile.get("proactive_topic_last_sent") or {}
    cooldown = timedelta(
        hours=max(18, int(profile.get("proactive_topic_cooldown_hours", 12)))
    )
    for topic, _message in offers:
        raw = sent.get(topic)
        if not isinstance(raw, str):
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if now - parsed < cooldown:
            return True
    return False


def _pick_offer(
    offers: tuple[tuple[str, str], ...],
    *,
    owner_id: str,
    now: datetime,
    profile: dict,
    recent_assistant: set[str],
) -> tuple[str, str] | None:
    start = int(
        hashlib.sha256(f"{owner_id}:{now.date().isoformat()}".encode()).hexdigest()[:8],
        16,
    ) % len(offers)
    for offset in range(len(offers)):
        topic, message = offers[(start + offset) % len(offers)]
        if message.casefold() in recent_assistant:
            continue
        if _available(profile, topic, now):
            return topic, message
    return None


def build_concierge_candidates(
    owner_id: str,
    profile: dict,
    history: Iterable[tuple[str, str]],
    now: datetime | None = None,
) -> list[dict]:
    """Build bounded offers from explicit context plus safe context-free fallbacks."""
    if profile.get("proactive_concierge_enabled") is not True:
        return []
    now = now or datetime.now(timezone.utc)
    categories = set(settings(profile)["categories"])
    rows = [(str(role), str(message).strip()) for role, message in history]
    user_messages = [
        message
        for role, message in rows
        if role == "user" and message and not _ACKNOWLEDGEMENT.fullmatch(message)
    ][-6:]
    recent_assistant = {
        message.casefold() for role, message in rows if role == "assistant" and message
    }
    candidates: list[dict] = []

    matched_topics: set[str] = set()
    for message in reversed(user_messages):
        for (
            topic,
            category,
            kind,
            pattern,
            offer,
            urgency,
            usefulness,
        ) in _OPPORTUNITIES:
            if category not in categories or topic in matched_topics:
                continue
            if not pattern.search(message) or not _available(profile, topic, now):
                continue
            matched_topics.add(topic)
            candidates.append(
                {
                    "message": offer,
                    "reason": (
                        "A recent user message explicitly described work Curie can help "
                        "prepare without taking an external action."
                    ),
                    "topic": topic,
                    "kind": kind,
                    "category": category,
                    "confidence": 1.0,
                    "urgency": urgency,
                    "usefulness": usefulness,
                    "priority": 0.78,
                    "evidence": [
                        {
                            "source": "recent_conversation",
                            "reference": f"explicit {topic} signal",
                            "confidence": 1.0,
                            "observed_at": now.isoformat(),
                        }
                    ],
                }
            )
            break

    if "ease" in categories and not _category_recently_sent(profile, _EASE_OFFERS, now):
        selected = _pick_offer(
            _EASE_OFFERS,
            owner_id=owner_id,
            now=now,
            profile=profile,
            recent_assistant=recent_assistant,
        )
        if selected:
            topic, message = selected
            candidates.append(
                {
                    "message": message,
                    "reason": "You enabled proactive life-easing offers.",
                    "topic": topic,
                    "kind": "life_easier",
                    "category": "ease",
                    "confidence": 1.0,
                    "urgency": 0.05,
                    "usefulness": 0.65,
                    "priority": 0.56,
                }
            )

    if "fun" in categories and not _category_recently_sent(profile, _FUN_OFFERS, now):
        selected = _pick_offer(
            _FUN_OFFERS,
            owner_id=f"fun:{owner_id}",
            now=now,
            profile=profile,
            recent_assistant=recent_assistant,
        )
        if selected:
            topic, message = selected
            candidates.append(
                {
                    "message": message,
                    "reason": "You enabled occasional original, low-pressure diversions.",
                    "topic": topic,
                    "kind": "entertainment",
                    "category": "fun",
                    "confidence": 1.0,
                    "urgency": 0.0,
                    "usefulness": 0.5,
                    "priority": 0.55,
                }
            )
    return candidates
