# connectors/teams.py
"""
Microsoft Teams connector – transport-only concerns.

Receives messages from Microsoft Teams via the Bot Framework webhook,
normalises them to the standard ChatWorkflow format, and returns responses.

Architecture
------------
The connector exposes a FastAPI sub-application mounted on the main app.
Teams sends HTTP POST requests to the bot's messaging endpoint.

                ┌────────────────────────────┐
  Teams ───────►│  /teams/messages  (webhook)│◄──► This connector
  (Bot Framework)└────────────────────────────┘

Setup
-----
1. Create an Azure Bot resource and register a messaging endpoint::

       https://<your-host>/teams/messages

2. Enable the Microsoft Teams channel in the Azure Bot dashboard.

3. Set environment variables::

       TEAMS_APP_ID=<your-microsoft-app-id>
       TEAMS_APP_PASSWORD=<your-microsoft-app-password>

Environment variables
---------------------
TEAMS_APP_ID         – Microsoft App (Client) ID from Azure AD
TEAMS_APP_PASSWORD   – Microsoft App password / client secret

Dependencies
------------
Install the optional Bot Framework SDK::

    pip install botbuilder-core botbuilder-integration-aiohttp
"""

import datetime
import logging
import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response

from agent.chat_workflow import ChatWorkflow
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (set by main.py)
_workflow: Optional[ChatWorkflow] = None

# Sub-application that main FastAPI app mounts at /teams
teams_app = FastAPI(title="Curie AI – Teams connector")


def set_workflow(workflow: ChatWorkflow) -> None:
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def _get_internal_id(teams_user_id: str) -> str:
    """Map a Teams user ID to the internal UUID used across all platforms."""
    return UserManager.get_or_create_user_internal_id(
        channel="teams",
        external_id=teams_user_id,
        secret_username=f"teams_{teams_user_id}",
        updated_by="teams_bot",
    )


@teams_app.post("/messages")
async def teams_messages(request: Request) -> Response:
    """
    Bot Framework messaging endpoint.

    Teams sends Activity objects here.  We extract the text, process it
    through ChatWorkflow, and reply using the Bot Framework REST API.

    Authentication
    --------------
    The Bot Framework sends a signed JWT Bearer token in the Authorization
    header.  We verify that the header is present and well-formed here.
    For full production validation (checking iss/aud/appid JWT claims),
    install and configure ``botbuilder-core``.
    """
    if not _workflow:
        return Response(content="System not initialized", status_code=503)

    # ── Basic Bot Framework auth check ──────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        logger.warning(
            "Teams webhook: missing or malformed Authorization header – rejecting request."
        )
        return Response(content="Unauthorized", status_code=401)

    try:
        body = await request.json()
    except Exception:
        return Response(content="Invalid JSON", status_code=400)

    activity_type = body.get("type", "")
    if activity_type != "message":
        # Acknowledge non-message activities (typing, etc.) silently
        return Response(status_code=200)

    text = body.get("text", "").strip()
    teams_user_id = (body.get("from") or {}).get("id", "unknown")
    conversation_id = (body.get("conversation") or {}).get("id", teams_user_id)
    service_url = body.get("serviceUrl", "")
    activity_id = body.get("id", str(uuid.uuid4()))

    if not text:
        return Response(status_code=200)

    internal_id = _get_internal_id(teams_user_id)
    normalized_input = {
        "platform": "teams",
        "external_user_id": teams_user_id,
        "external_chat_id": conversation_id,
        "message_id": activity_id,
        "text": text,
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }

    result = await _workflow.process_message(normalized_input)
    response_text = result.get("text", "[Error: No response]")

    # Reply via Bot Framework REST API
    if service_url:
        await _send_teams_reply(body, response_text, service_url)

    return Response(status_code=200)


async def _send_teams_reply(
    original_activity: dict, reply_text: str, service_url: str
) -> None:
    """Send a reply back to Teams via the Bot Framework REST API."""
    import httpx

    app_id = os.getenv("TEAMS_APP_ID", "")
    app_password = os.getenv("TEAMS_APP_PASSWORD", "")

    if not app_id or not app_password:
        logger.warning(
            "TEAMS_APP_ID / TEAMS_APP_PASSWORD not set – reply not sent."
        )
        return

    try:
        # Obtain access token from Microsoft identity platform
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app_id,
                    "client_secret": app_password,
                    "scope": "https://api.botframework.com/.default",
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            conversation_id = (original_activity.get("conversation") or {}).get(
                "id", ""
            )
            activity_id = original_activity.get("id", "")
            reply_url = (
                f"{service_url.rstrip('/')}/v3/conversations/"
                f"{conversation_id}/activities/{activity_id}"
            )

            reply_activity = {
                "type": "message",
                "text": reply_text,
                "replyToId": activity_id,
            }

            reply_resp = await client.post(
                reply_url,
                json=reply_activity,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if not reply_resp.is_success:
                logger.error(
                    "Teams reply failed: status=%s body=%s",
                    reply_resp.status_code,
                    reply_resp.text,
                )
    except Exception as exc:
        logger.error("Failed to send Teams reply: %s", exc)


def mount_on(app: FastAPI) -> None:
    """Mount the Teams sub-application on the main FastAPI app."""
    app.mount("/teams", teams_app)
    logger.info("Microsoft Teams connector mounted at /teams/messages")
