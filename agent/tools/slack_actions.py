# agent/tools/slack_actions.py
"""
Slack Actions tool — send messages and perform actions via Slack.

Allows Curie AI to post messages to Slack channels or DM users through
the Slack connector (``connectors/slack_bot.py``).

Natural-language triggers
--------------------------
  "send slack message to #general: deployment done"
  "post to slack channel #alerts: server down"
  "dm slack user U012AB3CD: your build passed"
  "notify slack #dev-team: new release available"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "slack_actions"

_SLACK_KEYWORDS = re.compile(
    r"\bslack\b.*(send|message|dm|post|notify|alert|ping)|"
    r"(send|post|notify).*\bslack\b",
    re.IGNORECASE,
)


def is_tool_query(message: str) -> bool:
    return bool(_SLACK_KEYWORDS.search(message))


def _extract_target_and_text(message: str):
    """Return (channel_or_user, text) or (None, None)."""
    # "#channel: text" or "channel #general: text"
    m = re.search(
        r"(?:to\s+)?(?:channel\s+)?(?P<ch>#[\w\-]+)\s*[:\-–]\s*(?P<text>.+)",
        message,
        re.I | re.DOTALL,
    )
    if m:
        return m.group("ch"), m.group("text").strip()
    # "dm user UXXX: text"
    m = re.search(
        r"(?:dm\s+)?(?:user\s+)?(?P<uid>U[A-Z0-9]{8,})\s*[:\-–]\s*(?P<text>.+)",
        message,
        re.I | re.DOTALL,
    )
    if m:
        return m.group("uid"), m.group("text").strip()
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
            "💬 Slack: could not parse target and message text.\n"
            "Try: *send slack message to #general: Hello team!*"
        )

    try:
        from connectors.slack_bot import send_message  # noqa: PLC0415

        success = await send_message(channel=target, text=text)
        if success:
            return f"💬 Slack message sent to **{target}**."
        return f"💬 Slack: failed to send message to **{target}** (bot may be offline)."
    except ImportError:
        return "💬 Slack connector not available (set SLACK_BOT_TOKEN to enable)."
    except Exception as exc:
        logger.error("slack_actions send error: %s", exc)
        return f"💬 Slack error: {exc}"
