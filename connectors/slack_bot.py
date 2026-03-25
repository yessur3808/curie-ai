# connectors/slack_bot.py
"""
Slack connector — transport-only concerns.

Receives Slack events (messages in channels and DMs), normalises them to the
standard Curie ``normalized_input`` format, and calls ``ChatWorkflow``.

Requires:
  * ``slack-bolt`` (optional dependency — install with ``pip install slack-bolt``)
  * ``SLACK_BOT_TOKEN`` env var  (``xoxb-…``)
  * ``SLACK_APP_TOKEN`` env var  (``xapp-…``, for Socket Mode)
  * ``SLACK_SIGNING_SECRET`` env var (app signing secret, for HTTP mode)

Socket Mode (recommended for local / development deployments) uses a
persistent WebSocket connection — no public URL required.  Set
``SLACK_SOCKET_MODE=true`` (default) to enable it.

Usage (via main.py / curie start --slack)::

    from connectors.slack_bot import start_slack_bot, set_workflow
    set_workflow(workflow)
    start_slack_bot(workflow)
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    SLACK_AVAILABLE = bool(App) and bool(SocketModeHandler)
except ImportError:
    App = None
    SocketModeHandler = None
    SLACK_AVAILABLE = False

from agent.chat_workflow import ChatWorkflow
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared workflow instance (set by main.py / set_workflow)
_workflow: Optional[ChatWorkflow] = None
# Bolt App instance (set during start_slack_bot)
_slack_app: Optional[Any] = None


def set_workflow(workflow: ChatWorkflow) -> None:
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


# ---------------------------------------------------------------------------
# User identity helpers
# ---------------------------------------------------------------------------


def _get_internal_id(slack_user_id: str, slack_username: str = "") -> str:
    """Look up or create an internal user ID for a Slack user."""
    return UserManager.get_or_create_user_internal_id(
        channel="slack",
        external_id=slack_user_id,
        secret_username=slack_username or f"slack_{slack_user_id}",
        updated_by="slack_bot",
    )


# ---------------------------------------------------------------------------
# Proactive message delivery
# ---------------------------------------------------------------------------


async def send_message(external_user_id: str, message: str) -> bool:
    """
    Send a proactive DM to a Slack user by their Slack user ID.

    Used by ProactiveMessagingService to deliver due reminders and check-ins.
    Returns True on success, False otherwise.
    """
    if _slack_app is None:
        logger.warning("Slack app not initialised; cannot send proactive message")
        return False
    try:
        # Open (or retrieve) a DM channel with the user, then send the message
        dm_response = _slack_app.client.conversations_open(users=external_user_id)
        channel_id = dm_response["channel"]["id"]
        _slack_app.client.chat_postMessage(
            channel=channel_id,
            text=message,
        )
        return True
    except Exception as exc:
        logger.error(
            "Failed to send Slack proactive message to %s: %s", external_user_id, exc
        )
        return False


# ---------------------------------------------------------------------------
# Bolt App factory & event handlers
# ---------------------------------------------------------------------------


def _build_slack_app(workflow: ChatWorkflow) -> "App":
    """Create and configure the Bolt App with all event handlers."""
    if not SLACK_AVAILABLE:
        raise RuntimeError(
            "slack-bolt is not installed.  Install it with:\n"
            "  pip install slack-bolt"
        )

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")

    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN environment variable is not set")

    app = App(token=bot_token, signing_secret=signing_secret)

    # ── Message handler ───────────────────────────────────────────────────
    @app.message("")
    def handle_message(message: dict, say: Any, client: Any) -> None:  # type: ignore[name-defined]
        """Handle all incoming messages (channels + DMs)."""
        if not _workflow:
            say("❌ System not initialised.")
            return

        # Ignore bot messages to avoid loops
        if message.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
            return
        if message.get("bot_id"):
            return

        user_id: str = message.get("user", "")
        channel_id: str = message.get("channel", "")
        msg_ts: str = message.get("ts", "")
        text: str = message.get("text", "") or ""

        if not text.strip():
            return

        # Resolve username (best-effort; fall back to user ID)
        slack_username = user_id
        try:
            info = client.users_info(user=user_id)
            profile = info.get("user", {}).get("profile", {})
            slack_username = (
                profile.get("display_name")
                or profile.get("real_name")
                or user_id
            )
        except Exception:
            pass

        internal_id = _get_internal_id(user_id, slack_username)

        normalized_input = {
            "platform": "slack",
            "external_user_id": user_id,
            "external_chat_id": channel_id,
            "message_id": msg_ts,
            "text": text,
            "timestamp": datetime.datetime.utcnow(),
            "internal_id": internal_id,
        }

        try:
            import asyncio

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_workflow.process_message(normalized_input))
            loop.close()
        except Exception as exc:
            logger.error("Error processing Slack message: %s", exc)
            say("❌ Sorry, an error occurred processing your message.")
            return

        response_text: str = result.get("text", "[Error: No response]")

        # Slack has a 4 000-character limit per block; split if needed
        if len(response_text) <= 4000:
            say(response_text)
        else:
            chunks = [response_text[i : i + 4000] for i in range(0, len(response_text), 4000)]
            for chunk in chunks:
                say(chunk)

    # ── Slash commands ─────────────────────────────────────────────────────

    @app.command("/curie")
    def handle_curie_command(ack: Any, body: dict, say: Any) -> None:  # type: ignore[name-defined]
        """Handle /curie slash command — forwards text to ChatWorkflow."""
        ack()
        text: str = body.get("text", "").strip()
        user_id: str = body.get("user_id", "")
        channel_id: str = body.get("channel_id", "")

        if not text:
            say("Usage: `/curie <your message>`")
            return

        if not _workflow:
            say("❌ System not initialised.")
            return

        internal_id = _get_internal_id(user_id)
        normalized_input = {
            "platform": "slack",
            "external_user_id": user_id,
            "external_chat_id": channel_id,
            "message_id": body.get("trigger_id", ""),
            "text": text,
            "timestamp": datetime.datetime.utcnow(),
            "internal_id": internal_id,
        }

        try:
            import asyncio

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(_workflow.process_message(normalized_input))
            loop.close()
        except Exception as exc:
            logger.error("Error handling /curie command: %s", exc)
            say("❌ An error occurred.")
            return

        say(result.get("text", "[No response]"))

    @app.command("/curie-help")
    def handle_help_command(ack: Any, say: Any) -> None:  # type: ignore[name-defined]
        """Show available Slack commands."""
        ack()
        say(
            "*Curie AI — Slack Commands*\n\n"
            "• Just send a message in any channel where Curie is present\n"
            "• `/curie <message>` — send a message via slash command\n"
            "• `/curie-help` — show this help message\n\n"
            "_Curie is also available on Telegram, Discord, and the REST API._"
        )

    @app.command("/curie-clear")
    def handle_clear_command(ack: Any, body: dict, say: Any) -> None:  # type: ignore[name-defined]
        """Clear conversation memory for the requesting user."""
        ack()
        user_id: str = body.get("user_id", "")
        try:
            from memory.session_store import get_session_manager

            sm = get_session_manager()
            sm.reset_session("slack", user_id)
            say("✅ Your conversation memory has been cleared.")
        except Exception as exc:
            logger.error("Error clearing Slack session: %s", exc)
            say("❌ Could not clear memory.")

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start_slack_bot(workflow: Optional[ChatWorkflow] = None) -> None:
    """
    Start the Slack bot in Socket Mode (blocking call).

    If *workflow* is provided, it is registered before the bot starts.
    Otherwise the module-level ``_workflow`` (set by ``set_workflow``) is used.
    """
    global _slack_app

    if not SLACK_AVAILABLE:
        logger.error(
            "slack-bolt is not installed.  Install with: pip install slack-bolt"
        )
        return

    if workflow is not None:
        set_workflow(workflow)

    if _workflow is None:
        logger.error("No ChatWorkflow set — call set_workflow() before start_slack_bot()")
        return

    app_token = os.getenv("SLACK_APP_TOKEN")
    use_socket_mode = os.getenv("SLACK_SOCKET_MODE", "true").lower() != "false"

    try:
        _slack_app = _build_slack_app(_workflow)
    except (RuntimeError, ValueError) as exc:
        logger.error("Failed to build Slack app: %s", exc)
        return

    if use_socket_mode:
        if not app_token:
            logger.error(
                "SLACK_APP_TOKEN is required for Socket Mode. "
                "Set SLACK_SOCKET_MODE=false to use HTTP mode."
            )
            return
        logger.info("🔌 Starting Slack bot in Socket Mode…")
        handler = SocketModeHandler(_slack_app, app_token)
        handler.start()
    else:
        signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        if not signing_secret:
            logger.error(
                "SLACK_SIGNING_SECRET is required for HTTP mode. "
                "Set SLACK_SOCKET_MODE=true to use Socket Mode instead."
            )
            return
        port = int(os.getenv("SLACK_PORT", "3000"))
        logger.info("🌐 Starting Slack bot in HTTP mode on port %d…", port)
        _slack_app.start(port=port)
