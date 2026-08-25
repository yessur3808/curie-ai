# connectors/slack.py
"""
Slack connector – transport-only concerns.

Receives Slack events via Socket Mode (no public URL required), normalises
them to the standard ChatWorkflow format, and returns responses.

Requirements
------------
Install the optional Slack dependencies::

    pip install slack-bolt

Environment variables
---------------------
SLACK_BOT_TOKEN   – Bot OAuth token  (xoxb-...)
SLACK_APP_TOKEN   – App-level token  (xapp-...)  [required for Socket Mode]

Both tokens are created in the Slack app dashboard at https://api.slack.com/apps.
"""

import asyncio
import datetime
import logging
import os
import threading
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from dotenv import load_dotenv

try:
    from slack_bolt import App as SlackApp
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    SLACK_BOLT_AVAILABLE = True
except ImportError:
    SLACK_BOLT_AVAILABLE = False

from agent.chat_workflow import ChatWorkflow
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (set by main.py)
_workflow: Optional[ChatWorkflow] = None

# Dedicated asyncio event loop running in a background thread so that async
# ChatWorkflow calls can be dispatched from Slack Bolt's sync event handlers
# without creating a new event loop for every incoming message.
_async_loop: Optional[asyncio.AbstractEventLoop] = None
_async_loop_lock = threading.Lock()


def _ensure_async_loop() -> asyncio.AbstractEventLoop:
    """Return the long-lived event loop, creating it on first call."""
    global _async_loop
    with _async_loop_lock:
        if _async_loop is None or _async_loop.is_closed():
            _async_loop = asyncio.new_event_loop()
            t = threading.Thread(target=_async_loop.run_forever, daemon=True)
            t.start()
    return _async_loop


def _run_async(coro):
    """Schedule a coroutine on the persistent event loop and block until done."""
    loop = _ensure_async_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def set_workflow(workflow: ChatWorkflow) -> None:
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def _get_internal_id(slack_user_id: str) -> str:
    """Map a Slack user ID to the internal UUID used across all platforms."""
    return UserManager.get_or_create_user_internal_id(
        channel="slack",
        external_id=slack_user_id,
        secret_username=f"slack_{slack_user_id}",
        updated_by="slack_bot",
    )


def _download_slack_file(file_info: dict, bot_token: str) -> tuple[str, str, str]:
    """Download one authenticated Slack file with strict host and size bounds."""
    url = file_info.get("url_private_download") or file_info.get("url_private") or ""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not (
            parsed.hostname == "slack.com" or parsed.hostname.endswith(".slack.com")
        )
    ):
        raise ValueError("Slack returned an invalid private file URL")
    max_bytes = int(os.getenv("SLACK_MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024)))
    if int(file_info.get("size") or 0) > max_bytes:
        raise ValueError(
            "That Slack attachment is too large. The current limit is 20 MB."
        )
    filename = file_info.get("name") or f"slack_{file_info.get('id', uuid.uuid4())}"
    fd, path = tempfile.mkstemp(prefix="curie_slack_", suffix=Path(filename).suffix)
    os.close(fd)
    total = 0
    try:
        with requests.get(
            url,
            headers={"Authorization": f"Bearer {bot_token}"},
            stream=True,
            timeout=30,
        ) as response:
            response.raise_for_status()
            with open(path, "wb") as output:
                for chunk in response.iter_content(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            "That Slack attachment is too large. The current limit is 20 MB."
                        )
                    output.write(chunk)
        return path, filename, file_info.get("mimetype", "")
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


def start_slack_bot(workflow: ChatWorkflow) -> None:
    """Start the Slack bot using Socket Mode with the shared ChatWorkflow."""
    global _workflow
    _workflow = workflow

    if not SLACK_BOLT_AVAILABLE:
        raise RuntimeError(
            "slack-bolt is not installed. Install it with: pip install slack-bolt"
        )

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")

    if not bot_token:
        raise RuntimeError("SLACK_BOT_TOKEN is not set. Add it to your .env file.")
    if not app_token:
        raise RuntimeError(
            "SLACK_APP_TOKEN is not set (required for Socket Mode). "
            "Add it to your .env file."
        )

    slack_app = SlackApp(token=bot_token)

    # ── Message handler ──────────────────────────────────────────────────────

    @slack_app.event("message")
    def handle_message(event, say, ack):
        """Handle incoming Slack messages."""
        ack()

        if not _workflow:
            say("❌ System not initialized.")
            return

        # Ignore messages from bots (including self) to prevent loops
        if event.get("bot_id"):
            return

        slack_user_id = event.get("user", "unknown")
        channel_id = event.get("channel", slack_user_id)
        ts = event.get("ts", "")
        text = event.get("text", "") or ""
        files = event.get("files") or []

        if not text and not files:
            return

        internal_id = _get_internal_id(slack_user_id)
        if files:
            say("I’m checking the attachment now, mon ami.")
        for file_info in files[:5]:
            media_path = None
            try:
                media_path, filename, content_type = _download_slack_file(
                    file_info, bot_token
                )
                from services.media_ingestion import prepare_attachment_message

                text = _run_async(
                    prepare_attachment_message(
                        media_path,
                        filename,
                        text,
                        content_type=content_type,
                        persona=_workflow.persona,
                    )
                )
            except Exception as exc:
                logger.warning("Slack attachment processing failed: %s", exc)
                say(str(exc))
                return
            finally:
                if media_path:
                    Path(media_path).unlink(missing_ok=True)

        normalized_input = {
            "platform": "slack",
            "external_user_id": slack_user_id,
            "external_chat_id": channel_id,
            "message_id": ts or str(uuid.uuid4()),
            "text": text,
            "timestamp": datetime.datetime.utcnow(),
            "internal_id": internal_id,
        }

        # Dispatch to the persistent shared event loop so a new loop is not
        # created for every message (Bolt sync handlers run in worker threads).
        result = _run_async(_workflow.process_message(normalized_input))
        response_text = result.get("text", "[Error: No response]")
        try:
            from services.voice_delivery import synthesize_reply, voice_replies_enabled

            if voice_replies_enabled(internal_id, "slack"):
                voice_path = _run_async(
                    synthesize_reply(response_text, _workflow.persona, internal_id)
                )
                if voice_path:
                    try:
                        slack_app.client.files_upload_v2(
                            channel=channel_id,
                            file=voice_path,
                            filename=os.path.basename(voice_path),
                            title="Curie voice reply",
                        )
                        return
                    finally:
                        Path(voice_path).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Slack voice reply failed; sending text: %s", exc)
        say(response_text)

    # ── App mention handler (@Curie …) ───────────────────────────────────────

    @slack_app.event("app_mention")
    def handle_mention(event, say, ack):
        """Handle @-mentions of the bot."""
        ack()
        handle_message(event, say, lambda: None)

    print("🤖 Slack bot is running (Socket Mode)…")
    handler = SocketModeHandler(slack_app, app_token)
    handler.start()
