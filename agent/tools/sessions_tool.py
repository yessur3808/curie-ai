# agent/tools/sessions_tool.py
"""
Sessions tool — inspect and manage user conversation sessions.

Natural-language triggers
--------------------------
  "list sessions"
  "show active sessions"
  "clear session for user abc123"
  "reset session on telegram for 42"
  "how many sessions are active"
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

TOOL_NAME = "sessions_tool"

_SESSION_KEYWORDS = re.compile(
    r"\bsession(s)?\b|\bconversation history\b|\bactive chat(s)?\b",
    re.IGNORECASE,
)


def is_tool_query(message: str) -> bool:
    return bool(_SESSION_KEYWORDS.search(message))


async def handle_tool_query(
    message: str,
    *,
    internal_id: str = "",
    platform: str = "",
    **_kwargs,
) -> Optional[str]:
    if not is_tool_query(message):
        return None

    try:
        from memory.session_store import get_session_manager  # noqa: PLC0415
    except ImportError:
        return "⚠️ Session manager unavailable."

    msg = message.strip()

    # --- list sessions ---
    if re.search(r"\b(list|show|display|view)\b.*session", msg, re.I):
        try:
            sm = get_session_manager()
            sessions = sm.list_sessions()
            if not sessions:
                return "💬 No active sessions found."
            lines = [f"💬 *Sessions* ({len(sessions)} total):"]
            for s in sessions[:20]:  # cap at 20
                uid = s.get("user_id", "?")
                ch = s.get("channel", "?")
                msg_count = len(s.get("messages", []))
                lines.append(f"  • `{ch}:{uid}` — {msg_count} messages")
            if len(sessions) > 20:
                lines.append(f"  … and {len(sessions) - 20} more")
            return "\n".join(lines)
        except Exception as exc:
            logger.error("sessions_tool list error: %s", exc)
            return "⚠️ Could not retrieve sessions."

    # --- reset / clear a specific session ---
    m = re.search(
        r"(?:clear|reset|wipe)\s+session\s+(?:for\s+user\s+)?(?P<uid>\S+)",
        msg,
        re.I,
    )
    if m:
        uid = m.group("uid")
        ch_match = re.search(r"on\s+(?P<ch>\w+)", msg, re.I)
        ch = ch_match.group("ch") if ch_match else (platform or "unknown")
        try:
            sm = get_session_manager()
            sm.reset_session(ch, uid)
            return f"💬 Session **{ch}:{uid}** cleared."
        except Exception as exc:
            logger.error("sessions_tool reset error: %s", exc)
            return f"⚠️ Could not reset session: {exc}"

    # --- count / stats ---
    if re.search(r"\b(how many|count|stats?)\b.*session", msg, re.I):
        try:
            sm = get_session_manager()
            sessions = sm.list_sessions()
            return f"💬 There are **{len(sessions)}** conversation session(s) on record."
        except Exception as exc:
            logger.error("sessions_tool stats error: %s", exc)
            return "⚠️ Could not count sessions."

    return None
