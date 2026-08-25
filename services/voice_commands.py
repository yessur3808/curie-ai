"""Connector-neutral voice configuration commands."""

from __future__ import annotations

from memory.adaptation import (
    get_preferences,
    set_custom_voice_consent,
    set_voice_preference,
)
from services.custom_voice import custom_voice_health
from services.voice_delivery import voice_health

VOICE_SAMPLE_TEXT = (
    "Bonjour, mon ami. I’m Curie. I’ll keep my English clear, my French natural, "
    "and my voice gentle enough to listen to comfortably."
)

VOICE_HELP = """Voice commands:
/voice on|off|status — choose voice or text replies
/voice_profile clear|soft|expressive|french|custom
/voice_accent neutral|subtle|strong
/voice_speed slow|normal|fast
/voice_warmth neutral|gentle|warm
/voice_expression calm|balanced|expressive
/voice_sample [optional text] — hear the current settings
/voice_custom status|consent|revoke|enroll
/voice_help — show this guide

Custom enrollment requires explicit consent and a reply to a voice note from a consenting speaker."""

_SETTING_MAP = {
    "profile": "voice_profile",
    "accent": "voice_accent",
    "speed": "voice_speed",
    "warmth": "voice_warmth",
    "expression": "voice_expressiveness",
}


def voice_status(owner_id: str, channel: str = "telegram") -> str:
    preferences = get_preferences(owner_id)
    enabled = preferences.get("voice_reply_channels", {}).get(
        channel, preferences.get("voice_reply", False)
    )
    health = voice_health()
    custom = custom_voice_health(preferences)
    return (
        f"Voice replies: {'on' if enabled else 'off'} on {channel}\n"
        f"Profile: {preferences.get('voice_profile', 'soft')}\n"
        f"Accent: {preferences.get('voice_accent', 'subtle')}\n"
        f"Speed: {preferences.get('voice_speed', 'normal')}\n"
        f"Warmth: {preferences.get('voice_warmth', 'gentle')}\n"
        f"Expression: {preferences.get('voice_expressiveness', 'balanced')}\n"
        f"Backend: {health.get('backend') or 'text fallback'}; bilingual: "
        f"{'ready' if health.get('bilingual_ready') else 'not ready'}\n"
        f"Custom voice: {'ready' if custom['ready'] else 'not ready'}"
    )


def configure_voice(owner_id: str, setting: str, value: str) -> str:
    key = _SETTING_MAP[setting]
    profile = set_voice_preference(owner_id, key, value)
    return f"Voice {setting} set to {profile['preferences'][key]}."


def custom_voice_command(owner_id: str, action: str) -> str:
    action = action.strip().casefold() or "status"
    if action == "consent":
        set_custom_voice_consent(owner_id, True)
        return (
            "Custom voice enrollment is enabled. Reply to a voice note from a "
            "consenting speaker with /voice_custom enroll."
        )
    if action in {"revoke", "disable"}:
        set_custom_voice_consent(owner_id, False)
        set_voice_preference(owner_id, "voice_profile", "soft")
        return "Custom voice consent revoked. Curie returned to the soft local profile."
    health = custom_voice_health(get_preferences(owner_id))
    return (
        "Custom voice status: "
        f"consent={'yes' if health['consented'] else 'no'}, "
        f"engine={'installed' if health['engine_installed'] else 'missing'}, "
        f"model={'configured' if health['model_configured'] else 'missing'}, "
        f"reference={'configured' if health['reference_configured'] else 'missing'}, "
        f"ready={'yes' if health['ready'] else 'no'}."
    )
