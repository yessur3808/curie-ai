import asyncio
import os
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from agent import action_router
from agent.intent_router import classify_request
from agent.skills.headless_browser import fill_label
from agent.tooling import ToolContext, get_runtime_registry
from connectors.google_oauth import (
    GMAIL_READ_SCOPE,
    GMAIL_SEND_SCOPE,
    authorization_url as google_url,
)
from connectors.twitter import authorization_url as x_url
from memory import local_store
from services import credential_vault


@pytest.fixture()
def integration_env(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    monkeypatch.setenv("CURIE_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CURIE_CREDENTIAL_STORE", str(tmp_path / "credentials.enc"))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-secret")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "https://curie.example/oauth/google/callback"
    )
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "x-client")
    monkeypatch.setenv("X_OAUTH_CLIENT_SECRET", "x-secret")
    monkeypatch.setenv("X_OAUTH_REDIRECT_URI", "https://curie.example/oauth/x/callback")
    return tmp_path


def test_credential_vault_is_encrypted_and_private(integration_env):
    credential_vault.put_credential("owner", "x", {"refresh_token": "very-secret"})
    path = integration_env / "credentials.enc"
    assert b"very-secret" not in path.read_bytes()
    assert os.stat(path).st_mode & 0o077 == 0
    assert (
        credential_vault.get_credential("owner", "x")["refresh_token"] == "very-secret"
    )
    assert credential_vault.get_credential("other", "x") is None


def test_google_and_x_oauth_use_pkce_and_full_requested_scopes(integration_env):
    google = parse_qs(urlsplit(google_url("owner", product="gmail", write=True)).query)
    assert set(google["scope"][0].split()) == {GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE}
    assert google["code_challenge_method"] == ["S256"]
    assert "owner" not in google["state"][0]

    x = parse_qs(urlsplit(x_url("owner")).query)
    assert {
        "tweet.read",
        "tweet.write",
        "dm.read",
        "dm.write",
        "offline.access",
    } <= set(x["scope"][0].split())
    assert x["code_challenge_method"] == ["S256"]


@pytest.mark.parametrize(
    ("text", "action", "approval"),
    [
        ("/gmail search is:unread", "gmail_search", False),
        ("/gmail send a@example.com | Hello | Body", "gmail_send", True),
        ("/x search curie ai", "x_search", False),
        ("/x post Hello world", "x_post", True),
        ("/x reply 123456 | Thanks", "x_reply", True),
        ("/x dm read", "x_dm_read", False),
        ("/x dm send 987654 | Hello", "x_dm_send", True),
        ("/browser open https://example.com", "browser_open", False),
        ("/browser click Continue", "browser_click", True),
    ],
)
def test_account_commands_are_typed_and_writes_require_approval(text, action, approval):
    request = classify_request(text)
    assert request.action == action
    assert request.needs_approval is approval


def test_x_post_cannot_bypass_fresh_approval(integration_env, monkeypatch):
    called = []

    async def fake_post(owner_id, text, reply_to=None):
        called.append((owner_id, text, reply_to))
        return {"data": {"id": "123", "text": text}}

    monkeypatch.setattr("connectors.twitter.create_post", fake_post)
    request = classify_request("/x post Approved content")
    preview = asyncio.run(action_router.execute_request(request, "owner", {}))
    assert "/approve action" in preview and called == []
    pending_audit = local_store.list_action_audit("owner")[0]
    assert pending_audit["details"]["parameters"]["text"] != "Approved content"
    token = preview.split("/approve action ", 1)[1][:8]
    result = asyncio.run(
        action_router.execute_request(
            action_router.ToolRequest("approve", {"token": token}), "owner", {}
        )
    )
    assert "Posted to X" in result
    assert called == [("owner", "Approved content", None)]

    with pytest.raises(PermissionError):
        asyncio.run(
            get_runtime_registry().execute(
                "x_post", {"text": "Bypass"}, ToolContext("owner")
            )
        )


def test_browser_refuses_authentication_secret_fields_before_launch():
    with pytest.raises(PermissionError, match="authentication secrets"):
        asyncio.run(fill_label("owner", "Password", "do-not-store"))
