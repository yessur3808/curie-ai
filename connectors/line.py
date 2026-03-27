# connectors/line.py
"""
LINE connector – transport-only concerns.

Receives messages from the LINE Messaging API via a webhook, normalises them
to the standard ChatWorkflow format, and returns responses.

Architecture
------------
The connector exposes a FastAPI sub-application mounted on the main app.
LINE sends HTTP POST requests to the configured webhook URL.

                ┌────────────────────────────┐
  LINE ────────►│  /line/webhook  (webhook)  │◄──► This connector
  Messaging API └────────────────────────────┘

Setup
-----
1. Create a LINE Official Account and enable the Messaging API at
   https://developers.line.biz/console/

2. Set the webhook URL in the LINE developer console::

       https://<your-host>/line/webhook

3. Set environment variables::

       LINE_CHANNEL_ACCESS_TOKEN=<your-channel-access-token>
       LINE_CHANNEL_SECRET=<your-channel-secret>

Dependencies
------------
Install the optional LINE SDK::

    pip install line-bot-sdk

Environment variables
---------------------
LINE_CHANNEL_ACCESS_TOKEN  – Channel access token from LINE Developer Console
LINE_CHANNEL_SECRET        – Channel secret (used for signature validation)
"""

import datetime
import hashlib
import hmac
import logging
import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response

from agent.chat_workflow import ChatWorkflow
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (set by main.py)
_workflow: Optional[ChatWorkflow] = None

_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")

# Sub-application that the main FastAPI app mounts at /line
line_app = FastAPI(title="Curie AI – LINE connector")


def set_workflow(workflow: ChatWorkflow) -> None:
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def _get_internal_id(line_user_id: str) -> str:
    """Map a LINE user ID to the internal UUID used across all platforms."""
    return UserManager.get_or_create_user_internal_id(
        channel="line",
        external_id=line_user_id,
        secret_username=f"line_{line_user_id}",
        updated_by="line_bot",
    )


def _verify_line_signature(body_bytes: bytes, x_line_signature: str) -> bool:
    """Validate the X-Line-Signature header to verify the request is from LINE."""
    if not _CHANNEL_SECRET:
        return True  # Skip validation if secret is not configured
    expected = hmac.new(
        _CHANNEL_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).digest()
    import base64

    return hmac.compare_digest(
        base64.b64encode(expected).decode("utf-8"),
        x_line_signature,
    )


async def _reply_to_line(reply_token: str, text: str) -> None:
    """Send a reply message to a LINE user via the Reply API."""
    import httpx

    if not _CHANNEL_ACCESS_TOKEN:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN is not set – reply not sent.")
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "https://api.line.me/v2/bot/message/reply",
                headers={
                    "Authorization": f"Bearer {_CHANNEL_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "replyToken": reply_token,
                    "messages": [{"type": "text", "text": text}],
                },
            )
    except Exception as exc:
        logger.error("Failed to send LINE reply: %s", exc)


@line_app.post("/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: str = Header(default=""),
) -> Response:
    """
    LINE Messaging API webhook endpoint.

    LINE sends Webhook Event objects here.  We extract text messages,
    process them through ChatWorkflow, and reply via the Reply API.
    """
    if not _workflow:
        return Response(content="System not initialized", status_code=503)

    body_bytes = await request.body()

    if _CHANNEL_SECRET and not _verify_line_signature(body_bytes, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid LINE signature")

    try:
        body = await request.json()
    except Exception:
        return Response(content="Invalid JSON", status_code=400)

    for event in body.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        text = msg.get("text", "").strip()
        line_user_id = (event.get("source") or {}).get("userId", "unknown")
        reply_token = event.get("replyToken", "")
        event_id = event.get("webhookEventId", str(uuid.uuid4()))

        if not text:
            continue

        internal_id = _get_internal_id(line_user_id)
        normalized_input = {
            "platform": "line",
            "external_user_id": line_user_id,
            "external_chat_id": line_user_id,
            "message_id": event_id,
            "text": text,
            "timestamp": datetime.datetime.utcnow(),
            "internal_id": internal_id,
        }

        result = await _workflow.process_message(normalized_input)
        response_text = result.get("text", "[Error: No response]")

        if reply_token:
            await _reply_to_line(reply_token, response_text)

    return Response(status_code=200)


def mount_on(app: FastAPI) -> None:
    """Mount the LINE sub-application on the main FastAPI app."""
    app.mount("/line", line_app)
    logger.info("LINE connector mounted at /line/webhook")
