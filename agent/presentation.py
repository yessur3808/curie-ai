"""Zero-inference presentation shared by generated and deterministic replies."""

import hashlib
from collections.abc import Mapping

_DEFAULTS = {
    "unavailable": "I'm not ready to answer yet. Please try again shortly.",
    "attachment": "I'll check that attachment.",
    "voice_unheard": "I couldn't make out that voice message. Please resend it or type it.",
    "weather": "Here's the weather outlook for {city}.",
    "analytics": "Here's the latest from your analytics.",
    "research": "A research finding worth a closer look.",
    "sources_saved": "Source links are available on request.",
    "resumed": "I'm ready to chat again.",
    "recovery_ok": "Recovery checks found no action needed.",
    "recovery_limited": "Recovery limit reached. Operator assistance is required.",
}


def voice_spec(persona: Mapping) -> str:
    configured = persona.get("presentation", {}).get("compact")
    if configured:
        return str(configured)
    # Never silently assign Curie's nationality/personality to another persona.
    style = persona.get("response_style", {})
    return f"{persona.get('name', 'Assistant')}: {style.get('tone', 'clear and helpful')}. {style.get('humor', '')}".strip()


def message(kind: str, *, persona=None, seed="", english_only=False, **values) -> str:
    """Render approved prose only; values are inserted verbatim, never rewritten."""
    if kind not in _DEFAULTS:
        raise ValueError("unknown presentation template")
    if persona is None:
        from agent.persona_contract import active_persona

        persona = active_persona()
    templates = persona.get("presentation", {}).get("templates", {})
    template = _DEFAULTS[kind] if english_only else templates.get(kind, _DEFAULTS[kind])
    if isinstance(template, list):
        if not template:
            template = _DEFAULTS[kind]
        else:
            index = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16)
            template = template[index % len(template)]
    return str(template).format_map(values)
