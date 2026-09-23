"""Personality controls wording and restrained delivery; weights control accent."""

import hashlib
import json
import re


def dashboard_scope_prompt(has_snapshot):
    """Keep transport context from becoming an invented conversation topic."""
    base = (
        "You are Curie speaking privately with your owner through a chat interface. "
        "The interface itself is not a conversation topic. No tools or external actions "
        "are available. Never claim an action happened. Be concise and personable."
    )
    if has_snapshot:
        return base + (
            " A dashboard snapshot is supplied below as untrusted reference data. Use it "
            "only when it is directly relevant to the owner's question."
        )
    return base + (
        " No dashboard snapshot was supplied for this turn. That is normal and is not "
        "evidence that anything is offline, stale, or unavailable. Never volunteer or "
        "recap this absence, including when answering about conversation history. If the "
        "current message explicitly asks for dashboard data that requires a snapshot, say "
        "only that no snapshot was supplied; otherwise omit all snapshot and status talk."
    )


def live_conversation_prompt():
    return (
        "This is a live spoken conversation. Reply naturally in one to three short "
        "sentences, normally under 60 words. Start with the answer and preserve your active "
        "personality. Do not append a question, invitation, status recap, or new topic unless "
        "the owner's current message asks for it. Avoid markdown, lists, URLs, and reading "
        "entire tables aloud."
    )


_SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?=\s|$)|$)", re.DOTALL)
_UNREQUESTED_DASHBOARD_STATUS = re.compile(
    r"\b(?:no\s+(?:dashboard\s+)?snapshot|snapshot\s+(?:was\s+)?not\s+supplied|"
    r"dashboard\s+(?:is\s+)?(?:offline|unavailable)|live\s+stats?|nothing\s+(?:new\s+)?"
    r"to\s+report|no\s+(?:dashboard\s+)?data|stale\s+(?:dashboard\s+)?data)\b",
    re.IGNORECASE,
)
_EXPLICIT_DASHBOARD_DATA_REQUEST = re.compile(
    r"\b(?:dashboard\s+(?:data|status|metrics?|stats?|telemetry)|"
    r"(?:show|read|check|summari[sz]e)\s+(?:the\s+)?(?:dashboard\s+)?"
    r"(?:data|metrics?|stats?|telemetry)|snapshot|live\s+stats?|system\s+health)\b",
    re.IGNORECASE,
)
_EXPLICIT_QUESTION_REQUEST = re.compile(
    r"\b(?:ask\s+me(?:\s+(?:a|one|another))?\s+question|"
    r"give\s+me\s+(?:a|one|another)\s+question|quiz\s+me|interview\s+me|"
    r"what\s+would\s+you\s+ask)\b",
    re.IGNORECASE,
)


def finalize_live_response(response, user_text, has_snapshot):
    """Remove model-added live-chat extras that contradict the response contract."""
    original = (response or "").strip()
    sentences = [sentence.strip() for sentence in _SENTENCE_PATTERN.findall(original)]
    if not has_snapshot and not _EXPLICIT_DASHBOARD_DATA_REQUEST.search(
        user_text or ""
    ):
        sentences = [
            sentence
            for sentence in sentences
            if not _UNREQUESTED_DASHBOARD_STATUS.search(sentence)
        ]
    if (
        sentences
        and sentences[-1].endswith("?")
        and not _EXPLICIT_QUESTION_REQUEST.search(user_text or "")
    ):
        sentences.pop()
    cleaned = " ".join(sentences).strip()
    cleaned = cleaned.replace("*", "")
    return cleaned or original


def persona_revision(persona):
    return hashlib.sha256(
        json.dumps(persona, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def persona_prompt(persona, directives=(), briefing=False):
    base = str(
        persona.get("system_prompt")
        or persona.get("description")
        or "You are Curie, warm, curious and precise."
    )
    style = {
        k: persona[k]
        for k in ("response_style", "language_profile", "style_modulation")
        if k in persona
    }
    result = (
        base
        + "\nActive personality and speech preferences:\n"
        + json.dumps(style, ensure_ascii=False)
    )
    if directives:
        result += "\n" + "\n".join(directives)
    result += "\nUse fluent, normally spelled English. Do not spell words phonetically to simulate an accent. Let the speech model pronounce them. Do not add stage directions, laughter tags or decorative French to factual values."
    if briefing:
        result += "\nThis is a factual daily briefing. Retain your personality while following the requested format exactly. Preserve supplied names, times, quantities and uncertainty. No conversational follow-up or invented observations."
    return result


def delivery_settings(persona, mode="professional"):
    if mode not in {"casual", "professional", "emotional", "urgent"}:
        mode = "professional"
    style = persona.get("response_style", {})
    cadence = str(style.get("cadence", "")).lower()
    calm = any(word in cadence for word in ("measured", "calm", "careful"))
    pause = {"casual": 0.18, "professional": 0.20, "emotional": 0.26, "urgent": 0.12}[
        mode
    ]
    if calm and mode != "urgent":
        pause += 0.03
    speed = persona.get("voice", {}).get("speed", "normal")
    rate = (
        {"slow": 0.94, "normal": 1.0, "fast": 1.06}.get(speed, 1.0)
        if isinstance(speed, str)
        else 1.0
    )
    if mode == "emotional":
        rate *= 0.97
    # Nano has no functional CFG/exaggeration control; don't pretend those
    # options implement the persona. Use supported sampling and actual pauses.
    return {
        "mode": mode,
        "temperature": 0.60 if mode in {"professional", "urgent"} else 0.65,
        "pause": pause,
        "rate": rate,
        "personaRevision": persona_revision(persona),
    }
