# connectors/kakaotalk.py
"""
KakaoTalk connector – transport-only concerns.

Receives messages from the KakaoTalk i Open Builder skill server webhook,
normalises them to the standard ChatWorkflow format, and returns responses.

Architecture
------------
The connector exposes a FastAPI sub-application mounted on the main app.
The Kakao i Open Builder sends HTTP POST requests to the skill server URL.

                ┌────────────────────────────────┐
  KakaoTalk ──►│  /kakao/webhook  (skill server) │◄──► This connector
  i Open Builder└────────────────────────────────┘

Setup
-----
1. Create a KakaoTalk chatbot at https://i.kakao.com/
2. Add a skill and set the skill server URL::

       https://<your-host>/kakao/webhook

Kakao i Open Builder sends a JSON body conforming to their SkillPayload spec.
The connector returns a JSON body conforming to their SkillResponse spec.

Note: Kakao i Open Builder does not provide a request signing mechanism for
skill server calls.  Restrict access to the endpoint at the network/proxy
layer (e.g. IP allowlist) rather than relying on application-layer secrets.
"""

import datetime
import logging
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.chat_workflow import ChatWorkflow
from memory import UserManager

load_dotenv()
logger = logging.getLogger(__name__)

# Shared ChatWorkflow instance (set by main.py)
_workflow: Optional[ChatWorkflow] = None

# Sub-application that the main FastAPI app mounts at /kakao
kakao_app = FastAPI(title="Curie AI – KakaoTalk connector")


def set_workflow(workflow: ChatWorkflow) -> None:
    """Set the shared ChatWorkflow instance (called from main.py)."""
    global _workflow
    _workflow = workflow


def _get_internal_id(kakao_user_id: str) -> str:
    """Map a KakaoTalk user ID to the internal UUID used across all platforms."""
    return UserManager.get_or_create_user_internal_id(
        channel="kakaotalk",
        external_id=kakao_user_id,
        secret_username=f"kakaotalk_{kakao_user_id}",
        updated_by="kakao_bot",
    )


def _build_skill_response(text: str) -> dict:
    """Build a Kakao i Open Builder SkillResponse payload."""
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text,
                    }
                }
            ]
        },
    }


@kakao_app.post("/webhook")
async def kakao_webhook(request: Request) -> JSONResponse:
    """
    Kakao i Open Builder skill server endpoint.

    Kakao sends SkillPayload objects here.  We extract the user utterance,
    process it through ChatWorkflow, and return a SkillResponse payload.
    """
    if not _workflow:
        return JSONResponse(
            content=_build_skill_response("❌ System not initialized."),
            status_code=503,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"error": "Invalid JSON"}, status_code=400)

    # Extract utterance text (Kakao SkillPayload v2 schema)
    utterance = (body.get("userRequest") or {}).get("utterance", "").strip()
    kakao_user_id = (
        ((body.get("userRequest") or {}).get("user") or {})
        .get("id", "unknown")
    )
    block_id = (body.get("action") or {}).get("id", str(uuid.uuid4()))

    if not utterance:
        return JSONResponse(
            content=_build_skill_response("Please send a text message."),
            status_code=200,
        )

    internal_id = _get_internal_id(kakao_user_id)
    normalized_input = {
        "platform": "kakaotalk",
        "external_user_id": kakao_user_id,
        "external_chat_id": kakao_user_id,
        "message_id": block_id,
        "text": utterance,
        "timestamp": datetime.datetime.utcnow(),
        "internal_id": internal_id,
    }

    result = await _workflow.process_message(normalized_input)
    response_text = result.get("text", "[Error: No response]")

    return JSONResponse(
        content=_build_skill_response(response_text),
        status_code=200,
    )


def mount_on(app: FastAPI) -> None:
    """Mount the KakaoTalk sub-application on the main FastAPI app."""
    app.mount("/kakao", kakao_app)
    logger.info("KakaoTalk connector mounted at /kakao/webhook")
