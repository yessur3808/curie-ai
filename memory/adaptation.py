"""Evidence-based, bounded presentation adaptation for each user."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any

_MIN_IMPLICIT_SAMPLES = 3
_DEFAULTS = {
    "verbosity": "balanced",
    "tone": "warm",
    "notification_cadence_hours": 24,
    "research_depth": "balanced",
    "response_layout": "adaptive",
    "preferred_tools": [],
    "voice_reply": False,
    "voice_reply_channels": {},
    "voice_quiet_channels": [],
    "voice_speed": "normal",
    "voice_warmth": "gentle",
    "voice_expressiveness": "balanced",
    "voice_profile": "soft",
    "voice_accent": "subtle",
    "custom_voice_consent": False,
    "custom_voice_reference": "",
}
_ALLOWED = {
    "verbosity": {"concise", "balanced", "detailed"},
    "tone": {"warm", "neutral", "professional"},
    "research_depth": {"quick", "balanced", "deep"},
    "response_layout": {"adaptive", "structured"},
    "voice_speed": {"slow", "normal", "fast"},
    "voice_warmth": {"neutral", "gentle", "warm"},
    "voice_expressiveness": {"calm", "balanced", "expressive"},
    "voice_profile": {"clear", "soft", "expressive", "french", "custom"},
    "voice_accent": {"neutral", "subtle", "strong"},
}
_PROTECTED = re.compile(
    r"\b(?:race|ethnicity|religion|sexual orientation|gender identity|disability|"
    r"medical condition|political affiliation|union membership|genetic|biometric)\b",
    re.I,
)
_TOO_LONG = re.compile(
    r"\b(?:too long|too verbose|be shorter|more concise|tldr)\b", re.I
)
_TOO_SHORT = re.compile(
    r"\b(?:too short|more detail|be more detailed|expand on that)\b", re.I
)
_WRONG = re.compile(
    r"\b(?:wrong answer|that's wrong|that is wrong|incorrect|you got that wrong)\b",
    re.I,
)
_CORRECTION = re.compile(r"\b(?:actually|correction|i meant|use this instead)\b", re.I)
_STYLE = re.compile(
    r"\buse (?:a |this )?(warm|neutral|professional) (?:tone|style)\b", re.I
)
_STRUCTURED = re.compile(
    r"\b(?:format|structure)\b.{0,60}\b(?:cleaner|better|clearly|bullets?|tables?)\b|"
    r"\buse\b.{0,40}\b(?:bullets?|tables?)\b.{0,40}\b(?:when needed|as needed|where useful)\b",
    re.I,
)
_COMMAND = re.compile(
    r"^/?adaptation(?:\s+(inspect|pause|resume|set))?(?:\s+(.*))?$", re.I
)
_RESET = re.compile(r"^/?reset_preferences(?:\s+([a-z_]+))?$", re.I)
_VOICE_COMMAND = re.compile(
    r"^(?:/voice(?:\s+(on|off|status))?|(?:please\s+)?reply\s+(?:to\s+me\s+)?with\s+voice|"
    r"(?:please\s+)?(?:send|give)\s+me\s+(?:a\s+)?voice\s+(?:message|note)|"
    r"(?:please\s+)?(?:respond|answer)\s+(?:to\s+me\s+)?(?:in|with)\s+(?:a\s+)?voice\s+(?:message|note)|"
    r"(?:stop|disable|turn\s+off)\s+voice\s+repl(?:y|ies))$",
    re.I,
)
_TEXT_MODE_REQUEST = re.compile(
    r"\b(?:reply|respond|answer|explain|write|send)(?:\s+(?:it|that|this))?(?:\s+to\s+me)?\s+"
    r"(?:in|as)\s+(?:plain\s+)?text\b|\btext(?:\s+reply|\s+response)?\s+only\b",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _database():
    from memory import database

    return database.mongo_db


def _load(owner_id: str) -> dict:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import get_adaptation_profile

        stored = get_adaptation_profile(owner_id)
    else:
        stored = _database().adaptation_profiles.find_one({"owner_id": owner_id}) or {}
    return {
        "owner_id": owner_id,
        "version": int(stored.get("version", 0)),
        "enabled": stored.get("enabled", True),
        "preferences": {**_DEFAULTS, **stored.get("preferences", {})},
        "history": list(stored.get("history", []))[-50:],
        "updated_at": stored.get("updated_at"),
    }


def _save(profile: dict) -> None:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import save_adaptation_profile

        save_adaptation_profile(profile["owner_id"], profile)
        return
    _database().adaptation_profiles.update_one(
        {"owner_id": profile["owner_id"]}, {"$set": profile}, upsert=True
    )


def _event(owner_id: str, signal: str, details: dict) -> None:
    document = {**details, "recorded_at": _now()}
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import add_adaptation_event

        add_adaptation_event(owner_id, signal, document)
        return
    _database().adaptation_events.insert_one(
        {"owner_id": owner_id, "signal": signal, **document}
    )


def _events(owner_id: str, signal: str) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_adaptation_events

        return list_adaptation_events(owner_id, signal)
    return list(
        _database()
        .adaptation_events.find({"owner_id": owner_id, "signal": signal})
        .limit(100)
    )


def get_preferences(owner_id: str) -> dict:
    profile = _load(str(owner_id))
    return dict(profile["preferences"]) if profile["enabled"] else dict(_DEFAULTS)


def get_adaptation_state(owner_id: str) -> dict:
    """Return the versioned state for services that must distinguish defaults."""
    return _load(str(owner_id))


def apply_voice_modality_preference(
    owner_id: str, text: str, channel: str | None = None
) -> bool | None:
    """Persist an explicit embedded text/voice request without consuming its task."""
    if not channel or not _TEXT_MODE_REQUEST.search(text):
        return None
    overrides = dict(_load(owner_id)["preferences"].get("voice_reply_channels", {}))
    overrides[channel] = False
    _set(owner_id, "voice_reply_channels", overrides, "explicit:text_mode_request")
    return False


def _set(owner_id: str, setting: str, value: Any, source: str) -> dict:
    profile = _load(owner_id)
    before = profile["preferences"].get(setting)
    if before == value:
        return profile
    profile["version"] += 1
    profile["preferences"][setting] = value
    profile["history"].append(
        {
            "version": profile["version"],
            "setting": setting,
            "before": before,
            "after": value,
            "source": source,
            "created_at": _now(),
        }
    )
    profile["updated_at"] = _now()
    _save(profile)
    return profile


def set_voice_preference(owner_id: str, setting: str, value: str) -> dict:
    """Validate and persist one user-visible voice setting."""
    if setting not in {
        "voice_speed",
        "voice_warmth",
        "voice_expressiveness",
        "voice_profile",
        "voice_accent",
    }:
        raise ValueError(f"Unknown voice setting: {setting}")
    normalized = str(value).strip().casefold()
    if normalized not in _ALLOWED[setting]:
        raise ValueError(f"Choose one of: {', '.join(sorted(_ALLOWED[setting]))}.")
    return _set(owner_id, setting, normalized, "explicit:voice_setting")


def set_custom_voice_consent(owner_id: str, consent: bool) -> dict:
    """Persist explicit consent for owner-scoped voice-reference processing."""
    return _set(
        owner_id,
        "custom_voice_consent",
        bool(consent),
        "explicit:custom_voice_consent",
    )


def set_custom_voice_reference(owner_id: str, path: str) -> dict:
    """Store only a local owner-scoped reference path after explicit consent."""
    profile = _load(owner_id)
    if not profile["preferences"].get("custom_voice_consent"):
        raise PermissionError("Custom voice consent is required before enrollment.")
    return _set(
        owner_id,
        "custom_voice_reference",
        str(path),
        "explicit:custom_voice_enrollment",
    )


def record_explicit_feedback(owner_id: str, text: str) -> dict | None:
    """Record direct presentation feedback; never derive sensitive traits."""
    if not owner_id or not text or _PROTECTED.search(text):
        return None
    signal, setting, value = None, None, None
    if _TOO_LONG.search(text):
        signal, setting, value = "too_long", "verbosity", "concise"
    elif _TOO_SHORT.search(text):
        signal, setting, value = "too_short", "verbosity", "detailed"
    elif _STRUCTURED.search(text):
        signal, setting, value = "structured_layout", "response_layout", "structured"
    else:
        style = _STYLE.search(text)
        if style:
            signal, setting, value = "style_request", "tone", style.group(1).casefold()
        elif _WRONG.search(text):
            signal = "wrong"
        elif _CORRECTION.search(text):
            signal = "correction"
    if not signal:
        return None
    _event(owner_id, signal, {"explicit": True, "text": text[:300]})
    if setting:
        profile = _set(owner_id, setting, value, f"explicit:{signal}")
        return dict(profile["preferences"])
    return get_preferences(owner_id)


def record_explicit_event(owner_id: str, signal: str, text: str = "") -> bool:
    """Record a bounded explicit outcome without deriving personal attributes."""
    allowed = {"correction", "wrong", "proactive_rejection", "accepted_action"}
    if signal not in allowed or _PROTECTED.search(text):
        return False
    _event(owner_id, signal, {"explicit": True, "text": text[:300]})
    return True


def record_operational_signal(
    owner_id: str,
    signal: str,
    *,
    tool: str = "",
    latency_ms: float | None = None,
) -> dict:
    """Apply only bounded changes after repeated owner-scoped operational evidence."""
    allowed = {
        "regeneration",
        "tool_failure",
        "abandonment",
        "response_time",
        "accepted_action",
    }
    if signal not in allowed:
        raise ValueError(f"Unsupported adaptation signal: {signal}")
    details = {"explicit": False, "tool": tool[:64]}
    if latency_ms is not None:
        details["latency_ms"] = max(0.0, min(float(latency_ms), 300_000.0))
    _event(owner_id, signal, details)
    samples = _events(owner_id, signal)
    profile = _load(owner_id)
    if not profile["enabled"] or len(samples) < _MIN_IMPLICIT_SAMPLES:
        return dict(profile["preferences"])
    if signal == "abandonment":
        profile = _set(owner_id, "verbosity", "concise", "implicit:abandonment")
    elif signal == "regeneration":
        profile = _set(owner_id, "research_depth", "deep", "implicit:regeneration")
    elif signal in {"tool_failure", "accepted_action"} and tool:
        tools = list(profile["preferences"].get("preferred_tools", []))
        matching = [item for item in samples if item.get("tool") == tool]
        if len(matching) >= _MIN_IMPLICIT_SAMPLES:
            if signal == "accepted_action" and tool not in tools:
                tools = [*tools, tool][-10:]
            elif signal == "tool_failure":
                tools = [item for item in tools if item != tool]
            profile = _set(owner_id, "preferred_tools", tools, f"implicit:{signal}")
    return dict(profile["preferences"])


def handle_adaptation_command(
    owner_id: str, text: str, channel: str | None = None
) -> str | None:
    voice = _VOICE_COMMAND.fullmatch(text.strip())
    if voice:
        requested = (voice.group(1) or "").casefold()
        if not requested:
            requested = (
                "off" if re.search(r"\b(?:stop|disable|off)\b", text, re.I) else "on"
            )
        if requested == "status":
            preferences = _load(owner_id)["preferences"]
            overrides = preferences.get("voice_reply_channels", {})
            enabled = bool(overrides.get(channel, preferences.get("voice_reply")))
            scope = f" on {channel}" if channel else ""
            return f"Voice replies are {'on' if enabled else 'off'}{scope}."
        if channel:
            overrides = dict(
                _load(owner_id)["preferences"].get("voice_reply_channels", {})
            )
            overrides[channel] = requested == "on"
            _set(owner_id, "voice_reply_channels", overrides, "explicit:voice_command")
            state = "enabled" if requested == "on" else "disabled"
            return f"Voice replies are now {state} on {channel}."
        profile = _set(
            owner_id, "voice_reply", requested == "on", "explicit:voice_command"
        )
        return f"Voice replies are now {'on' if profile['preferences']['voice_reply'] else 'off'}."
    reset = _RESET.fullmatch(text.strip())
    if reset:
        setting = reset.group(1)
        if setting and setting not in _DEFAULTS:
            return f"Unknown adaptation setting `{setting}`."
        if setting:
            _set(owner_id, setting, _DEFAULTS[setting], "explicit:reset")
        else:
            if not os.getenv("MONGODB_URI"):
                from memory.local_store import reset_adaptation

                reset_adaptation(owner_id)
            else:
                _database().adaptation_profiles.delete_one({"owner_id": owner_id})
                _database().adaptation_events.delete_many({"owner_id": owner_id})
        return "Adaptation preferences reset."
    match = _COMMAND.fullmatch(text.strip())
    if not match:
        return None
    action, argument = (match.group(1) or "inspect").casefold(), (
        match.group(2) or ""
    ).strip()
    profile = _load(owner_id)
    if action in {"pause", "resume"}:
        profile["enabled"] = action == "resume"
        profile["version"] += 1
        profile["updated_at"] = _now()
        _save(profile)
        return f"Adaptation {'resumed' if profile['enabled'] else 'paused'}."
    if action == "set":
        parts = argument.split(None, 1)
        if len(parts) != 2:
            return "Use `/adaptation set <setting> <value>`."
        setting, raw = parts[0].casefold(), parts[1].strip()
        if setting in _ALLOWED and raw.casefold() in _ALLOWED[setting]:
            value: Any = raw.casefold()
        elif setting == "notification_cadence_hours":
            try:
                value = max(1, min(int(raw), 168))
            except ValueError:
                return "Notification cadence must be between 1 and 168 hours."
        elif setting == "preferred_tools":
            from agent.tooling import get_runtime_registry

            value = list(
                dict.fromkeys(item.strip() for item in raw.split(",") if item.strip())
            )[:10]
            unknown = set(value) - set(get_runtime_registry().names())
            if unknown:
                return f"Unknown registered tools: {', '.join(sorted(unknown))}."
        elif setting == "voice_reply" and raw.casefold() in {
            "on",
            "off",
            "true",
            "false",
        }:
            value = raw.casefold() in {"on", "true"}
        else:
            return f"Invalid value for adaptation setting `{setting}`."
        profile = _set(owner_id, setting, value, "explicit:command")
        return f"Set `{setting}` to `{profile['preferences'][setting]}` (version {profile['version']})."
    return json.dumps(profile, default=str, indent=2)
