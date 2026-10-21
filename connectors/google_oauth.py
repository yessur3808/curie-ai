"""Google OAuth PKCE flow plus Gmail and Calendar API adapters."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import hashlib
import os
import secrets
from urllib.parse import urlencode

import httpx

from services.credential_vault import delete_credential, get_credential, put_credential
from services.oauth_state import consume_oauth_state, save_oauth_state

CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_CALENDAR_API = "https://www.googleapis.com/calendar/v3"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _scopes(product: str, write: bool) -> tuple[str, ...]:
    if product == "calendar":
        return (
            (CALENDAR_READ_SCOPE, CALENDAR_WRITE_SCOPE)
            if write
            else (CALENDAR_READ_SCOPE,)
        )
    if product == "gmail":
        return (GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE) if write else (GMAIL_READ_SCOPE,)
    raise ValueError("Unknown Google OAuth product")


def authorization_url(owner_id: str, *, product: str, write: bool = False) -> str:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise RuntimeError("Google OAuth client ID and redirect URI are not configured")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    save_oauth_state(
        owner_id,
        {
            "id": state,
            "state": state,
            "provider": "google",
            "product": product,
            "write": bool(write),
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
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(_scopes(product, write)),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )


def _consume_state(state: str) -> tuple[str, dict]:
    return consume_oauth_state(state, provider="google")


async def exchange_authorization_code(state: str, code: str) -> tuple[str, str]:
    owner_id, pending = _consume_state(state)
    payload = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": os.environ["GOOGLE_OAUTH_REDIRECT_URI"],
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": pending["verifier"],
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(_TOKEN_URL, data=payload)
        response.raise_for_status()
        token = response.json()
    token["expires_at"] = (
        datetime.now(timezone.utc).timestamp() + int(token.get("expires_in", 3600)) - 60
    )
    token["product"], token["write"] = pending["product"], bool(pending["write"])
    put_credential(owner_id, f"google:{pending['product']}", token)
    return owner_id, str(pending["product"])


async def _access_token(owner_id: str, product: str = "gmail") -> str:
    token = get_credential(owner_id, f"google:{product}")
    if not token:
        raise PermissionError(f"Google {product} is not connected for this owner")
    if float(token.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp():
        return str(token["access_token"])
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise PermissionError("Google authorization expired; reconnect the account")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            _TOKEN_URL,
            data={
                "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
                "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        response.raise_for_status()
        token.update(response.json())
    token["refresh_token"] = refresh_token
    token["expires_at"] = (
        datetime.now(timezone.utc).timestamp() + int(token.get("expires_in", 3600)) - 60
    )
    put_credential(owner_id, f"google:{product}", token)
    return str(token["access_token"])


async def gmail_search(owner_id: str, query: str, limit: int = 10) -> list[dict]:
    access = await _access_token(owner_id)
    headers = {"Authorization": f"Bearer {access}"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{_GMAIL_API}/messages",
            headers=headers,
            params={"q": query[:500], "maxResults": max(1, min(limit, 20))},
        )
        response.raise_for_status()
        output = []
        for item in response.json().get("messages", []):
            detail = await client.get(
                f"{_GMAIL_API}/messages/{item['id']}",
                headers=headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            detail.raise_for_status()
            payload = detail.json()
            values = {
                row["name"].casefold(): row["value"]
                for row in payload.get("payload", {}).get("headers", [])
            }
            output.append(
                {
                    "id": payload.get("id"),
                    "thread_id": payload.get("threadId"),
                    "from": values.get("from", ""),
                    "subject": values.get("subject", "(no subject)"),
                    "date": values.get("date", ""),
                    "snippet": str(payload.get("snippet", ""))[:500],
                    "untrusted": True,
                }
            )
    return output


async def gmail_read(owner_id: str, message_id: str) -> dict:
    if not message_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid Gmail message ID")
    access = await _access_token(owner_id)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{_GMAIL_API}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access}"},
            params={"format": "full"},
        )
        response.raise_for_status()
        payload = response.json()
    values = {
        row["name"].casefold(): row["value"]
        for row in payload.get("payload", {}).get("headers", [])
    }

    def decode_part(part: dict) -> str:
        mime = str(part.get("mimeType", "")).casefold()
        encoded = str(part.get("body", {}).get("data", ""))
        if encoded and mime in {"text/plain", ""}:
            try:
                return base64.urlsafe_b64decode(
                    encoded + "=" * (-len(encoded) % 4)
                ).decode(errors="replace")
            except (ValueError, TypeError):
                return ""
        for child in part.get("parts", [])[:50]:
            value = decode_part(child)
            if value:
                return value
        return ""

    body = decode_part(payload.get("payload", {})) or str(payload.get("snippet", ""))
    return {
        "id": payload.get("id"),
        "from": values.get("from", ""),
        "to": values.get("to", ""),
        "subject": values.get("subject", "(no subject)"),
        "date": values.get("date", ""),
        "snippet": body[:10_000],
        "untrusted": True,
    }


async def gmail_send(owner_id: str, recipient: str, subject: str, body: str) -> dict:
    token = get_credential(owner_id, "google:gmail") or {}
    if not token.get("write"):
        raise PermissionError("Gmail send permission has not been granted")
    access = await _access_token(owner_id)
    message = EmailMessage()
    message["To"], message["Subject"] = recipient, subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{_GMAIL_API}/messages/send",
            headers={"Authorization": f"Bearer {access}"},
            json={"raw": raw},
        )
        response.raise_for_status()
        return response.json()


def _calendar_time(value: dict) -> str:
    return str(value.get("dateTime") or value.get("date") or "")


async def calendar_list_events(
    owner_id: str,
    *,
    time_min: str,
    time_max: str,
    query: str = "",
    limit: int = 10,
) -> list[dict]:
    """List a bounded, chronological event window from the primary calendar."""
    start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
    end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Calendar windows must include a timezone")
    if end <= start or end - start > timedelta(days=366):
        raise ValueError("Calendar window must be positive and no longer than one year")
    access = await _access_token(owner_id, "calendar")
    params = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max(1, min(int(limit), 50)),
    }
    if query.strip():
        params["q"] = query.strip()[:500]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{_CALENDAR_API}/calendars/primary/events",
            headers={"Authorization": f"Bearer {access}"},
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
    return [
        {
            "id": str(row.get("id") or "")[:200],
            "title": str(row.get("summary") or "Busy")[:300],
            "start": _calendar_time(row.get("start") or {}),
            "end": _calendar_time(row.get("end") or {}),
            "timezone": str(
                (row.get("start") or {}).get("timeZone")
                or (row.get("end") or {}).get("timeZone")
                or ""
            )[:100],
            "location": str(row.get("location") or "")[:500],
            "status": str(row.get("status") or "")[:40],
            "html_link": str(row.get("htmlLink") or "")[:1000],
            "untrusted": True,
        }
        for row in payload.get("items", [])[: max(1, min(int(limit), 50))]
        if row.get("status") != "cancelled"
    ]


async def calendar_create_event(
    owner_id: str,
    *,
    summary: str,
    start: str,
    end: str,
    timezone_name: str,
    description: str = "",
    location: str = "",
) -> dict:
    """Create one event after the tool layer has enforced fresh approval."""
    token = get_credential(owner_id, "google:calendar") or {}
    if not token.get("write"):
        raise PermissionError("Google Calendar write permission has not been granted")
    start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("Event start and end must include a timezone offset")
    if end_at <= start_at or end_at - start_at > timedelta(days=30):
        raise ValueError("Event end must follow start and be within 30 days")
    if not summary.strip():
        raise ValueError("Event title cannot be empty")
    access = await _access_token(owner_id, "calendar")
    body = {
        "summary": summary.strip()[:300],
        "start": {"dateTime": start_at.isoformat(), "timeZone": timezone_name[:100]},
        "end": {"dateTime": end_at.isoformat(), "timeZone": timezone_name[:100]},
    }
    if description.strip():
        body["description"] = description.strip()[:5000]
    if location.strip():
        body["location"] = location.strip()[:500]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{_CALENDAR_API}/calendars/primary/events",
            headers={"Authorization": f"Bearer {access}"},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
    return {
        "id": payload.get("id"),
        "status": payload.get("status"),
        "html_link": payload.get("htmlLink"),
        "summary": payload.get("summary"),
        "start": _calendar_time(payload.get("start") or {}),
        "end": _calendar_time(payload.get("end") or {}),
    }


def disconnect_google(owner_id: str, product: str = "gmail") -> bool:
    return delete_credential(owner_id, f"google:{product}")


def store_refresh_token(owner_id: str, product: str, token: str) -> None:
    put_credential(
        owner_id, f"google:{product}", {"refresh_token": token, "expires_at": 0}
    )


def load_refresh_token(owner_id: str, product: str) -> str | None:
    return (get_credential(owner_id, f"google:{product}") or {}).get("refresh_token")


def delete_refresh_token(owner_id: str, product: str) -> None:
    disconnect_google(owner_id, product)
