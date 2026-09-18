"""X API v2 OAuth and bounded read/write operations."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import os
import secrets
from urllib.parse import quote, urlencode

import httpx

from services.credential_vault import delete_credential, get_credential, put_credential
from services.oauth_state import consume_oauth_state, save_oauth_state

_AUTH_URL = "https://x.com/i/oauth2/authorize"
_TOKEN_URL = "https://api.x.com/2/oauth2/token"
_API = "https://api.x.com/2"
_POSTING_SCOPES = (
    "tweet.read",
    "tweet.write",
    "users.read",
    "offline.access",
)
_DM_SCOPES = ("dm.read", "dm.write")


def requested_scopes() -> tuple[str, ...]:
    """Request only posting scopes unless DM access is explicitly enabled."""
    allow_dms = os.getenv("X_OAUTH_DM_SCOPES_ENABLED", "false").strip().casefold()
    return _POSTING_SCOPES + (_DM_SCOPES if allow_dms in {"1", "true", "yes"} else ())


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(owner_id: str) -> str:
    client_id = os.getenv("X_OAUTH_CLIENT_ID")
    redirect_uri = os.getenv("X_OAUTH_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise RuntimeError("X OAuth client ID and redirect URI are not configured")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    save_oauth_state(
        owner_id,
        {
            "id": state,
            "state": state,
            "provider": "x",
            "verifier": verifier,
            "used": False,
            "expires_at": datetime.now(timezone.utc).timestamp() + 600,
        },
    )
    return (
        _AUTH_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(requested_scopes()),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def _consume_state(state: str) -> tuple[str, dict]:
    return consume_oauth_state(state, provider="x")


def _client_auth() -> tuple[dict, dict]:
    client_id = os.environ["X_OAUTH_CLIENT_ID"]
    secret = os.getenv("X_OAUTH_CLIENT_SECRET", "")
    if secret:
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {basic}"}, {}
    return {}, {"client_id": client_id}


async def exchange_authorization_code(state: str, code: str) -> str:
    owner_id, pending = _consume_state(state)
    headers, client_fields = _client_auth()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            _TOKEN_URL,
            headers=headers,
            data={
                **client_fields,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": os.environ["X_OAUTH_REDIRECT_URI"],
                "code_verifier": pending["verifier"],
            },
        )
        response.raise_for_status()
        token = response.json()
    token["expires_at"] = (
        datetime.now(timezone.utc).timestamp() + int(token.get("expires_in", 7200)) - 60
    )
    put_credential(owner_id, "x", token)
    return owner_id


async def _access_token(owner_id: str) -> str:
    token = get_credential(owner_id, "x")
    if not token:
        raise PermissionError("X is not connected for this owner")
    if float(token.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp():
        return str(token["access_token"])
    if not token.get("refresh_token"):
        raise PermissionError("X authorization expired; reconnect the account")
    headers, client_fields = _client_auth()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            _TOKEN_URL,
            headers=headers,
            data={
                **client_fields,
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            },
        )
        response.raise_for_status()
        refreshed = response.json()
    token.update(refreshed)
    token["expires_at"] = (
        datetime.now(timezone.utc).timestamp() + int(token.get("expires_in", 7200)) - 60
    )
    put_credential(owner_id, "x", token)
    return str(token["access_token"])


async def _request(owner_id: str, method: str, path: str, **kwargs) -> dict:
    token = await _access_token(owner_id)
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(
            method, f"{_API}{path}", headers=headers, **kwargs
        )
        response.raise_for_status()
        return response.json()


async def search_posts(owner_id: str, query: str, limit: int = 10) -> list[dict]:
    payload = await _request(
        owner_id,
        "GET",
        "/tweets/search/recent",
        params={
            "query": query[:512],
            "max_results": max(10, min(limit, 100)),
            "tweet.fields": "author_id,created_at,public_metrics,conversation_id",
            "expansions": "author_id",
            "user.fields": "name,username",
        },
    )
    users = {item["id"]: item for item in payload.get("includes", {}).get("users", [])}
    return [
        {**post, "author": users.get(post.get("author_id"), {}), "untrusted": True}
        for post in payload.get("data", [])
    ]


async def read_post(owner_id: str, post_id: str) -> dict:
    if not post_id.isdigit():
        raise ValueError("X post ID must be numeric")
    return await _request(
        owner_id,
        "GET",
        f"/tweets/{post_id}",
        params={
            "tweet.fields": "author_id,created_at,public_metrics,conversation_id",
            "expansions": "author_id",
            "user.fields": "name,username",
        },
    )


async def create_post(owner_id: str, text: str, reply_to: str | None = None) -> dict:
    if not text.strip() or len(text) > 280:
        raise ValueError("X post text must be between 1 and 280 characters")
    body: dict = {"text": text}
    if reply_to:
        if not reply_to.isdigit():
            raise ValueError("Reply post ID must be numeric")
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    return await _request(owner_id, "POST", "/tweets", json=body)


async def read_dms(owner_id: str, limit: int = 20) -> list[dict]:
    payload = await _request(
        owner_id,
        "GET",
        "/dm_events",
        params={
            "max_results": max(1, min(limit, 100)),
            "event_types": "MessageCreate",
            "dm_event.fields": "created_at,sender_id,text,dm_conversation_id",
            "expansions": "sender_id",
            "user.fields": "name,username",
        },
    )
    users = {item["id"]: item for item in payload.get("includes", {}).get("users", [])}
    return [
        {**event, "sender": users.get(event.get("sender_id"), {}), "untrusted": True}
        for event in payload.get("data", [])
    ]


async def send_dm(owner_id: str, participant_id: str, text: str) -> dict:
    if not participant_id.isdigit():
        raise ValueError("X participant ID must be numeric")
    return await _request(
        owner_id,
        "POST",
        f"/dm_conversations/with/{quote(participant_id, safe='')}/messages",
        json={"text": text[:10_000]},
    )


def disconnect_x(owner_id: str) -> bool:
    return delete_credential(owner_id, "x")
