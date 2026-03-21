# memory/learning.py
"""
Proactive filtered learning & enhancement system.

Automatically extracts user preferences, habits, and useful context from
conversations and persists them in the user's MongoDB profile.  Only
*high-signal* facts are stored; noise is filtered out.

How it works
------------
1. After every assistant turn, ``learn_from_exchange()`` is called with the
   latest (user_message, assistant_reply) pair.
2. A set of lightweight regex heuristics run first to decide whether the
   exchange *could* contain learnable facts (fast path).
3. If heuristics match, a short LLM prompt is used to extract structured
   facts as a JSON dict (slow path — only triggered for relevant messages).
4. Extracted facts are merged into the user's profile via ``UserManager``.

Stored fact categories
----------------------
- ``name``                 – preferred name / nickname
- ``timezone``             – user's timezone
- ``language``             – preferred language
- ``location``             – home city / country
- ``interests``            – list of topics they enjoy
- ``dietary_preferences``  – vegetarian, vegan, allergies, …
- ``occupation``           – job or field of work
- ``travel_style``         – budget / luxury / backpacker
- ``reminders_preference`` – how they like to be reminded
- (any other key the LLM deems relevant)

Environment variables
---------------------
ENABLE_LEARNING           Enable/disable the learning system (default: true)
LEARNING_LLM_PROVIDER     Provider to use for extraction (default: same priority as chat)
LEARNING_MAX_FACTS        Max facts to store per user (default: 50)
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_ENABLED = os.getenv("ENABLE_LEARNING", "true").strip().lower() != "false"
_MAX_FACTS = int(os.getenv("LEARNING_MAX_FACTS", "50"))

# ---------------------------------------------------------------------------
# Fast-path heuristics — only run LLM when these match
# ---------------------------------------------------------------------------

# Identity / location facts (who the user is and where they are)
_SIGNAL_IDENTITY = r"my name is|i am|i'm|call me|i live in|i'm from|i'm a"

# Preference facts (what the user likes/dislikes)
_SIGNAL_PREFERENCES = (
    r"i love|i like|i enjoy|i prefer|i hate|i dislike|i don't like|"
    r"i'm vegetarian|i'm vegan|i'm allergic"
)

# Habit / routine facts (patterns in the user's daily life)
_SIGNAL_HABITS = (
    r"i usually|i always|i never|i wake up at|i go to bed at|"
    r"i work from|i finish at|remind me (every|each|daily|weekly|morning|evening)"
)

# Professional / contextual facts
_SIGNAL_CONTEXT = (
    r"i work (as|in|at)|my timezone|my location|i speak|my language|"
    r"i travel|i visit|my job|my occupation|my hobby|my hobbies"
)

_SIGNAL_PATTERNS = re.compile(
    r"\b("
    + "|".join([_SIGNAL_IDENTITY, _SIGNAL_PREFERENCES, _SIGNAL_HABITS, _SIGNAL_CONTEXT])
    + r")\b",
    re.IGNORECASE,
)

_NOISE_PATTERNS = re.compile(
    r"\b(what is|how do|can you|please|thanks|thank you|ok|okay|yes|no|"
    r"sure|alright|great|nice|good|bad|help|assist)\b",
    re.IGNORECASE,
)


def _should_attempt_extraction(user_message: str) -> bool:
    """Quick heuristic: return True if the message might contain learnable facts."""
    if not _SIGNAL_PATTERNS.search(user_message):
        return False
    # Exclude very short or purely noise messages
    words = user_message.split()
    if len(words) < 4:
        return False
    return True


# ---------------------------------------------------------------------------
# LLM-based fact extractor
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a fact-extraction assistant. Read the user message below and extract any \
personal facts, preferences, or useful context that would help a personal assistant \
remember things about this user.

Return ONLY a valid JSON object mapping fact names (snake_case strings) to values. \
If there are no learnable facts, return an empty JSON object {{}}.

Rules:
- Only extract facts that are explicitly stated, never infer or assume.
- Keep values concise (max 100 chars each).
- Use lists for multi-valued facts (interests, dietary_preferences, etc.).
- Allowed fact keys: name, timezone, language, location, interests,
  dietary_preferences, occupation, travel_style, reminders_preference,
  wake_time, sleep_time, work_hours, hobbies, and any clearly personal fact.
- Do NOT extract: questions, commands, complaints, generic statements.

User message:
\"\"\"{message}\"\"\"

JSON only, no commentary:
"""


def _extract_facts_via_llm(user_message: str) -> dict:
    """
    Use the LLM to extract structured facts from the user message.
    Returns a dict (may be empty).
    """
    prompt = _EXTRACTION_PROMPT.format(message=user_message[:500])

    raw: Optional[str] = None
    try:
        from llm.providers import ask_best_provider  # noqa: PLC0415

        # max_tokens=256 is intentional here: the extraction prompt requests a
        # compact JSON object, and a generous token budget risks verbose output
        # that is harder to parse and wastes context space.
        raw = ask_best_provider(prompt, temperature=0.1, max_tokens=256)
    except Exception:
        pass

    if raw is None:
        try:
            from llm import manager as llm_manager  # noqa: PLC0415

            raw = llm_manager.ask_llm(prompt, temperature=0.1, max_tokens=256)
        except Exception:
            pass

    if not raw:
        return {}

    # Extract the first JSON object from the response
    json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not json_match:
        return {}

    try:
        facts = json.loads(json_match.group(0))
        if not isinstance(facts, dict):
            return {}
        # Sanitise: ensure all keys/values are reasonable types
        _PRIMITIVE_TYPES = (str, int, float, bool)
        clean: dict = {}
        for k, v in facts.items():
            if not isinstance(k, str) or not k.strip():
                continue
            key = re.sub(r"[^a-z0-9_]", "_", k.strip().lower())[:50]
            if isinstance(v, _PRIMITIVE_TYPES):
                clean[key] = v
            elif isinstance(v, list):
                # Only keep lists whose every element is a primitive type
                if all(isinstance(item, _PRIMITIVE_TYPES) for item in v):
                    clean[key] = v
                # Silently drop lists that contain complex/nested objects
        return clean
    except (json.JSONDecodeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Fact guard — prevent over-writing important / sensitive facts
# ---------------------------------------------------------------------------

_PROTECTED_KEYS = frozenset(
    [
        # These require explicit user action to set
        "internal_id",
        "proactive_messaging_enabled",
        "proactive_interval_hours",
        "roles",
        "is_master",
    ]
)


def _filter_facts(existing: dict, new_facts: dict) -> dict:
    """
    Return only the subset of new_facts that should be merged.
    Protects sensitive keys and caps total fact count.
    """
    merged: dict = {}
    total = len(existing)
    for k, v in new_facts.items():
        if k in _PROTECTED_KEYS:
            continue
        if total >= _MAX_FACTS and k not in existing:
            logger.debug(
                "Skipping new fact %r — user profile at max capacity (%d)",
                k,
                _MAX_FACTS,
            )
            continue
        # For list facts, merge rather than overwrite.
        # Use list() to avoid mutating the existing list in-place.
        if isinstance(v, list) and isinstance(existing.get(k), list):
            existing_set = set(str(x) for x in existing[k])
            new_items = [x for x in v if str(x) not in existing_set]
            if new_items:
                merged[k] = list(existing[k]) + new_items
        else:
            merged[k] = v
        total += 1
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def learn_from_exchange(
    internal_id: str,
    user_message: str,
    assistant_reply: str,  # noqa: ARG001  (reserved for future use)
) -> None:
    """
    Attempt to extract and persist learnable facts from a conversation turn.

    This function is intentionally non-blocking and exception-safe — a failure
    here must never interrupt the main chat flow.

    Parameters
    ----------
    internal_id:     Owner's internal user ID.
    user_message:    The user's latest message.
    assistant_reply: The assistant's reply (not currently used but kept for
                     future signal extraction from replies).
    """
    if not _ENABLED:
        return
    if not internal_id or not user_message:
        return
    if not _should_attempt_extraction(user_message):
        return

    try:
        new_facts = _extract_facts_via_llm(user_message)
        if not new_facts:
            return

        from memory.users import UserManager  # noqa: PLC0415

        existing = UserManager.get_user_profile(internal_id) or {}
        to_store = _filter_facts(existing, new_facts)
        if to_store:
            UserManager.update_user_profile(internal_id, to_store)
            logger.info(
                "Learning: stored %d fact(s) for user=%s — keys: %s",
                len(to_store),
                internal_id,
                list(to_store.keys()),
            )
    except Exception as exc:
        logger.debug("learn_from_exchange failed (non-critical): %s", exc)
