"""Personality controls wording and restrained delivery; weights control accent."""

import hashlib
import json


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
