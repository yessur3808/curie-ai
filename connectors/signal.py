# connectors/signal.py
"""
Signal connector – transport-only concerns.

Bridges the Curie AI workflow with Signal Messenger via a local
signal-cli REST API server (https://github.com/bbernhard/signal-cli-rest-api).

Architecture
------------
Signal does not provide an official bot API.  This connector uses the
signal-cli REST API, a lightweight self-hosted bridge that exposes Signal
over HTTP/WebSocket.

                ┌─────────────────────────────┐
  Signal ──────►│  signal-cli REST API (local)│◄──► This connector
  Network       └─────────────────────────────┘

Setup
-----
1. Run the signal-cli REST API server::

       docker run -p 8080:8080 bbernhard/signal-cli-rest-api

2. Register/link your Signal account via the REST API::

       curl -X POST http://localhost:8080/v1/register/+1234567890

3. Set environment variables::

       SIGNAL_CLI_API_URL=http://localhost:8080
       SIGNAL_PHONE_NUMBER=+1234567890          # bot's phone number

Environment variables
---------------------
SIGNAL_CLI_API_URL      – Base URL of the signal-cli REST API server
SIGNAL_PHONE_NUMBER     – The Signal phone number registered for the bot
SIGNAL_POLL_INTERVAL    – Seconds between message polling (default: 2)
"""

import asyncio
import datetime
import logging
import os
import time
import uuid
from typing import Optional

import requests
from dotenv import load_dotenv

from agent.chat_workflow import ChatWorkflow
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (set by main.py)
_workflow: Optional[ChatWorkflow] = None

_API_URL = os.getenv("SIGNAL_CLI_API_URL", "http://localhost:8080")
_PHONE = os.getenv("SIGNAL_PHONE_NUMBER", "")
_POLL_INTERVAL = float(os.getenv("SIGNAL_POLL_INTERVAL", "2"))


def set_workflow(workflow: ChatWorkflow) -> None:
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def _get_internal_id(phone_number: str) -> str:
    """Map a Signal phone number to the internal UUID used across all platforms."""
    return UserManager.get_or_create_user_internal_id(
        channel="signal",
        external_id=phone_number,
        secret_username=f"signal_{phone_number}",
        updated_by="signal_bot",
    )


def _send_signal_message(recipient: str, text: str) -> bool:
    """Send a Signal message via the signal-cli REST API."""
    try:
        resp = requests.post(
            f"{_API_URL}/v2/send",
            json={"message": text, "number": _PHONE, "recipients": [recipient]},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Failed to send Signal message to %s: %s", recipient, exc)
        return False


def _receive_signal_messages() -> list:
    """Poll for new Signal messages from the signal-cli REST API."""
    try:
        resp = requests.get(
            f"{_API_URL}/v1/receive/{_PHONE}",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as exc:
        logger.error("Failed to receive Signal messages: %s", exc)
        return []


def _process_envelope(envelope: dict) -> None:
    """Process a single Signal message envelope."""
    if not _workflow:
        return

    data_msg = envelope.get("dataMessage") or {}
    text = data_msg.get("message", "")
    source = envelope.get("source", "")

    if not text or not source:
        return

    internal_id = _get_internal_id(source)
    normalized_input = {
        "platform": "signal",
        "external_user_id": source,
        "external_chat_id": source,
        "message_id": str(uuid.uuid4()),
        "text": text,
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }

    # The Signal polling loop runs in a plain thread (no asyncio event loop),
    # so asyncio.run() is always safe to call directly here.
    result = asyncio.run(_workflow.process_message(normalized_input))
    response_text = result.get("text", "[Error: No response]")
    _send_signal_message(source, response_text)


def start_signal_bot(workflow: ChatWorkflow) -> None:
    """Start the Signal bot polling loop with the shared ChatWorkflow."""
    global _workflow
    _workflow = workflow

    if not _PHONE:
        raise RuntimeError(
            "SIGNAL_PHONE_NUMBER is not set. Add it to your .env file."
        )

    print(f"📡 Signal bot polling {_API_URL} as {_PHONE} …")

    while True:
        try:
            envelopes = _receive_signal_messages()
            for envelope in envelopes:
                try:
                    _process_envelope(envelope)
                except Exception as exc:
                    logger.error("Error processing Signal envelope: %s", exc)
        except Exception as exc:
            logger.error("Signal polling error: %s", exc)

        time.sleep(_POLL_INTERVAL)
