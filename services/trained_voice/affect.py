"""Transient, bounded voice cues and conservative response delivery selection."""

from collections import OrderedDict
from contextvars import ContextVar
import hashlib
import os
import re
import time

current_cues = ContextVar("curie_voice_cues", default=None)


def enabled():
    return os.getenv("CURIE_VOICE_AFFECT", "true").lower() in {"true", "1", "yes", "on"}


def safe_cues(value):
    if (
        not enabled()
        or not isinstance(value, dict)
        or value.get("status") != "available"
    ):
        return {}
    delivery = value.get("delivery")
    if delivery not in {"animated", "quiet", "steady"}:
        return {}
    return {"status": "available", "delivery": delivery, "confidence": "low"}


def cue_guidance(value=None):
    cue = safe_cues(current_cues.get() if value is None else value)
    if not cue:
        return ""
    return (
        f"Audio delivery cue: {cue['delivery']} (uncertain, microphone-dependent). "
        "Let the user's words lead. Adjust warmth and pacing gently; never claim "
        "to know their feelings from their voice or diagnose them. Don't mention this analysis."
    )


def response_style(text, cues=None):
    if not enabled():
        return "neutral"
    t = text.casefold().replace("’", "'")
    if re.search(r"\b(emergency|danger|urgent|warning)\b", t):
        return "clear"
    if re.search(
        r"\b(exhausted|upset|overwhelmed|scared|worried|grieving|sorry to hear|sounds difficult|that sounds exhausting)\b",
        t,
    ):
        return "gentle"
    if re.search(
        r"\b(congratulations|wonderful news|great news|well done|so happy|really excited)\b",
        t,
    ) and not re.search(r"\b(not|isn't|wasn't|don't)\b", t):
        return "bright"
    if safe_cues(cues).get("delivery") == "quiet":
        return "gentle"
    return "warm"


def expressive_delivery(delivery, text, cues=None):
    result = dict(delivery)
    if os.getenv("CURIE_EXPRESSIVE_OUTPUT", "false").lower() not in {
        "true",
        "1",
        "yes",
        "on",
    }:
        return result
    style = response_style(text, cues)
    if result.get("mode") in {"urgent", "professional"}:
        style = "clear"
    result["expression"] = style
    if style == "gentle":
        result.update(
            rate=round(float(result.get("rate", 1)) * 0.97, 4),
            pause=0.26,
            temperature=0.61,
        )
    elif style == "bright":
        result.update(
            rate=round(float(result.get("rate", 1)) * 1.03, 4),
            pause=0.16,
            temperature=0.70,
        )
    return result


class TurnCueStore:
    def __init__(self, capacity=64, ttl=120):
        self.rows = OrderedDict()
        self.capacity, self.ttl = capacity, ttl

    def _key(self, owner, text):
        return str(owner), hashlib.sha256(" ".join(text.split()).encode()).hexdigest()

    def put(self, owner, text, value):
        now = time.monotonic()
        for key in list(self.rows):
            if self.rows[key][0] <= now:
                del self.rows[key]
        key = self._key(owner, text)
        self.rows[key] = (now + self.ttl, value)
        self.rows.move_to_end(key)
        while len(self.rows) > self.capacity:
            self.rows.popitem(last=False)

    def take(self, owner, text):
        row = self.rows.pop(self._key(owner, text), None)
        return row[1] if row and row[0] > time.monotonic() else {}


incoming_cues = TurnCueStore()
outgoing_delivery = TurnCueStore()


class VoiceTranscript(str):
    def __new__(cls, text, cues=None):
        value = super().__new__(cls, text)
        value.voice_cues = safe_cues(cues)
        return value
