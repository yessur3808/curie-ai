# agent/tools/discord_actions.py
"""
Discord Actions tool — send messages and perform actions on Discord.

Allows Curie AI to proactively send DMs, post to channels, or look up
user information via the Discord connector.

Natural-language triggers
--------------------------
  "send discord message to user 123456: Hello!"
  "dm discord user 987654 about the build failure"
  "post to discord channel general: update complete"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "discord_actions"

_DISCORD_KEYWORDS = re.compile(
    r"\bdiscord\b.*(send|message|dm|post|notify|alert|ping)",
    re.IGNORECASE,
)


def is_tool_query(message: str) -> bool:
    return bool(_DISCORD_KEYWORDS.search(message))


def _extract_target_and_text(message: str):
    """Return (target_id, text) or (None, None)."""
    # "send discord message to user 123: text"
    m = re.search(
        r"(?:to\s+(?:user\s+)?|dm\s+(?:discord\s+)?(?:user\s+)?)(?P<uid>\d{15,22})\s*[:\-–]\s*(?P<text>.+)",
        message,
        re.I | re.DOTALL,
    )
    if m:
        return m.group("uid"), m.group("text").strip()
    # "post to discord channel general: text"
    m = re.search(
        r"post\s+to\s+(?:discord\s+)?channel\s+(?P<ch>\w+)\s*[:\-–]\s*(?P<text>.+)",
        message,
        re.I | re.DOTALL,
    )
    if m:
        return m.group("ch"), m.group("text").strip()
    return None, None


async def handle_tool_query(
    message: str,
    **_kwargs,
) -> Optional[str]:
    if not is_tool_query(message):
        return None

    target, text = _extract_target_and_text(message)
    if not target or not text:
        return (
            "🎮 Discord: could not parse target and message text.\n"
            "Try: *send discord message to user 123456: Hello!*"
        )

    try:
        from connectors.discord_bot import send_message  # noqa: PLC0415

        success = await send_message(external_user_id=target, message=text)
        if success:
            return f"🎮 Discord message sent to **{target}**."
        return f"🎮 Discord: failed to send message to **{target}** (bot may be offline)."
    except ImportError:
        return "🎮 Discord connector not available."
    except Exception as exc:
        logger.error("discord_actions send error: %s", exc)
        return f"🎮 Discord error: {exc}"
