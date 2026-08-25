"""Minimum-scope Google OAuth configuration with OS-keyring token storage."""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlencode

CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
_SERVICE = "curie-google-oauth"


def authorization_url(owner_id: str, *, product: str, write: bool = False) -> str:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    if not client_id or not redirect_uri:
        raise RuntimeError("Google OAuth client ID and redirect URI are not configured")
    if product == "calendar":
        scope = CALENDAR_WRITE_SCOPE if write else CALENDAR_READ_SCOPE
    elif product == "gmail":
        scope = GMAIL_SEND_SCOPE if write else GMAIL_READ_SCOPE
    else:
        raise ValueError("Unknown Google OAuth product")
    state = secrets.token_urlsafe(32)
    from memory.local_store import save_personal_item
    save_personal_item(owner_id, "oauth_state", {
        "id": state, "product": product, "write": write,
        "expires_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).timestamp() + 600,
    })
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": scope,
        "access_type": "offline", "prompt": "consent",
        "state": state,
    })


def store_refresh_token(owner_id: str, product: str, token: str) -> None:
    """Store refresh tokens only in the operating-system credential store."""
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Install and configure keyring before connecting Google") from exc
    keyring.set_password(_SERVICE, f"{product}:{owner_id}", token)


def load_refresh_token(owner_id: str, product: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring.get_password(_SERVICE, f"{product}:{owner_id}")


def delete_refresh_token(owner_id: str, product: str) -> None:
    try:
        import keyring
        keyring.delete_password(_SERVICE, f"{product}:{owner_id}")
    except Exception:
        return
