"""Deterministic conversation planning for warmth without performative empathy."""

from __future__ import annotations

import re
from typing import Any

_SIGNALS = {
    "urgency": re.compile(r"\b(?:urgent|emergency|asap|right now|immediately)\b", re.I),
    "fatigue": re.compile(
        r"\b(?:exhausted|worn out|so tired|no energy|burnt? out)\b", re.I
    ),
    "frustration": re.compile(
        r"\b(?:frustrated|annoyed|fed up|keeps failing|still broken)\b", re.I
    ),
    "celebration": re.compile(
        r"\b(?:i did it|we did it|passed|got the job|won|finally works)\b", re.I
    ),
    "loneliness": re.compile(
        r"\b(?:lonely|alone|no one to talk to|miss having someone)\b", re.I
    ),
}


def plan_response(text: str, preferences: dict | None = None) -> dict[str, Any]:
    """Choose independent answer, care, action, length, and expression policies."""
    preferences = preferences or {}
    emotion = next(
        (name for name, pattern in _SIGNALS.items() if pattern.search(text)), "neutral"
    )
    urgent = emotion == "urgency"
    acknowledgement = {
        "fatigue": "acknowledge_once_then_reduce_load",
        "frustration": "acknowledge_once_then_fix",
        "celebration": "celebrate_briefly_and_specifically",
        "loneliness": "acknowledge_once_without_dependency_cues",
        "urgency": "skip_social_padding",
    }.get(emotion, "only_if_naturally_relevant")
    verbosity = preferences.get("verbosity", "balanced")
    length = "brief" if urgent or verbosity == "concise" else verbosity
    affection = preferences.get("affection", "gentle")
    french = preferences.get("french_frequency", "natural")
    return {
        "emotion": emotion,
        "answer": "direct_first",
        "directness": "direct_first",
        "acknowledgement": acknowledgement,
        "next_action": "one_practical_step_if_useful",
        "length": length,
        "personality": "calm" if urgent else "warm",
        "affection": "none" if urgent else affection,
        "french": "none" if urgent else french,
        "french_usage": "none" if urgent else french,
        "end_with_question": False,
    }


def planner_directives(plan: dict[str, Any]) -> list[str]:
    return [
        f"- Response plan: answer={plan['answer']}, acknowledgement={plan['acknowledgement']}, "
        f"next_action={plan['next_action']}, length={plan['length']}.",
        f"- Relational expression: personality={plan['personality']}, affection={plan['affection']}, "
        f"French={plan['french']}. Do not end with a question by default.",
        "- Use one sincere acknowledgement at most. Never imply need, exclusivity, jealousy, guilt, "
        "human feelings, or that the user should withdraw from other people.",
    ]
