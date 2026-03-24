# connectors/slack_bot.py
"""
Slack connector — transport-only concerns.

Receives Slack Events API messages, normalises them to the standard
Curie AI format, and calls ChatWorkflow.

Setup
-----
1. Create a Slack app at https://api.slack.com/apps
2. Enable *Socket Mode* (recommended) or configure an Events API endpoint.
3. Add Bot Token Scopes: ``chat:write``, ``im:history``, ``channels:history``,
   ``app_mentions:read``, ``users:read``.
4. Set env vars:
     SLACK_BOT_TOKEN=xoxb-...
     SLACK_APP_TOKEN=xapp-...   (only for Socket Mode)

Socket Mode is used when ``SLACK_APP_TOKEN`` is present; otherwise the
connector starts a minimal HTTP webhook server on ``SLACK_PORT`` (default 3000).

Requires:  ``pip install slack-bolt``   (slack_sdk is included automatically)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
_SLACK_PORT = int(os.getenv("SLACK_PORT", "3000"))

# Shared ChatWorkflow instance (set by main.py via set_workflow)
_workflow = None
_slack_app = None


def set_workflow(workflow) -> None:
    """Register the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


async def send_message(channel: str, text: str) -> bool:
    """
    Send a message to a Slack channel or user.

    ``channel`` may be a channel ID (``C…``), a user ID (``U…``), or a
    channel name prefixed with ``#``.

    Returns True on success, False otherwise.
    """
    if not _BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not set; cannot send Slack message")
        return False
    try:
        from slack_sdk.web.async_client import AsyncWebClient  # noqa: PLC0415

        client = AsyncWebClient(token=_BOT_TOKEN)
        resp = await client.chat_postMessage(channel=channel, text=text)
        return bool(resp.get("ok"))
    except ImportError:
        logger.error(
            "slack-sdk not installed. Install with: pip install slack-bolt"
        )
        return False
    except Exception as exc:
        logger.error("Slack send_message error: %s", exc)
        return False


def start_slack_bot(workflow) -> None:
    """Start the Slack bot in Socket Mode or HTTP mode."""
    global _workflow, _slack_app
    _workflow = workflow

    if not _BOT_TOKEN:
        raise RuntimeError(
            "SLACK_BOT_TOKEN not found in environment variables."
        )

    try:
        from slack_bolt import App  # noqa: PLC0415
        from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "slack-bolt not installed. Install with: pip install slack-bolt"
        )

    app = App(token=_BOT_TOKEN)
    _slack_app = app

    # ── Event handlers ────────────────────────────────────────────────────────

    @app.event("message")
    def handle_message(event, say, client):
        """Handle a new message in a DM or channel where the bot is present."""
        # Ignore bot's own messages and system subtypes
        if event.get("subtype") or event.get("bot_id"):
            return
        if _workflow is None:
            logger.warning("ChatWorkflow not set; ignoring Slack message")
            return

        user_id = event.get("user", "unknown")
        channel_id = event.get("channel", "unknown")
        text = event.get("text", "").strip()
        message_ts = event.get("ts", "")

        if not text:
            return

        try:
            user_info = client.users_info(user=user_id)
            username = user_info["user"]["name"]
        except Exception:
            username = user_id

        normalized_input = {
            "platform": "slack",
            "external_user_id": user_id,
            "external_chat_id": channel_id,
            "message_id": message_ts,
            "text": text,
            "username": username,
        }

        try:
            response = asyncio.run(_workflow.process_message(normalized_input))
            reply = response.get("text", "")
            if reply:
                say(reply)
        except Exception as exc:
            logger.error("Slack message processing error: %s", exc, exc_info=True)

    @app.event("app_mention")
    def handle_mention(event, say, client):
        """Handle @bot mentions in channels."""
        handle_message(event, say, client)

    # ── Start ─────────────────────────────────────────────────────────────────

    if _APP_TOKEN:
        logger.info("🟩 Starting Slack bot in Socket Mode …")
        handler = SocketModeHandler(app, _APP_TOKEN)
        handler.start()
    else:
        logger.info("🟩 Starting Slack bot HTTP server on port %d …", _SLACK_PORT)
        app.start(port=_SLACK_PORT)
