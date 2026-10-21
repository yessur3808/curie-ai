"""Least-privilege GitHub App credential broker for Curie.

Long-lived personal access tokens remain an opt-in compatibility fallback. The
preferred path mints short-lived installation tokens from a private key stored
outside the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
from typing import Any


@dataclass(frozen=True, slots=True)
class GitHubAuthStatus:
    mode: str
    write_ready: bool
    detail: str
    expires_at: str = ""


class GitHubCredentialBroker:
    def __init__(self):
        self._lock = threading.Lock()
        self._cached: tuple[Any, datetime, str] | None = None

    @staticmethod
    def _app_configuration() -> tuple[str, Path | None, str]:
        app_id = os.getenv("GITHUB_APP_ID", "").strip()
        key_value = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
        installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID", "").strip()
        return (
            app_id,
            Path(key_value).expanduser() if key_value else None,
            installation_id,
        )

    def status(self) -> GitHubAuthStatus:
        app_id, key_path, installation_id = self._app_configuration()
        if app_id and key_path and installation_id:
            if not key_path.is_file():
                return GitHubAuthStatus(
                    "github_app", False, "private-key file is missing"
                )
            return GitHubAuthStatus(
                "github_app", True, "short-lived installation tokens enabled"
            )
        if os.getenv("GITHUB_TOKEN", "").strip():
            return GitHubAuthStatus(
                "legacy_token",
                True,
                "compatibility token enabled; migrate to a GitHub App",
            )
        return GitHubAuthStatus("anonymous", False, "read-only discovery only")

    def _app_client(self):
        app_id, key_path, installation_id = self._app_configuration()
        if not (app_id and key_path and installation_id):
            raise RuntimeError("GitHub App installation credentials are incomplete")
        if not key_path.is_file():
            raise RuntimeError(
                "The configured GitHub App private-key file does not exist"
            )
        # Refuse broad or accidentally repository-local key paths. Runtime
        # secrets belong under the owner's private configuration directory.
        if key_path.name in {".env", ".env.local"} or "curie-ai" in {
            part.casefold() for part in key_path.parts
        }:
            raise PermissionError(
                "The GitHub App private key must be stored outside the repository"
            )
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._cached and self._cached[1] > now + timedelta(minutes=2):
                return self._cached[0]
            from github import Auth, Github, GithubIntegration

            private_key = key_path.read_text(encoding="utf-8")
            integration = GithubIntegration(auth=Auth.AppAuth(app_id, private_key))
            token = integration.get_access_token(int(installation_id))
            expires_at = getattr(token, "expires_at", None) or now + timedelta(
                minutes=50
            )
            client = Github(auth=Auth.Token(str(token.token)))
            self._cached = (client, expires_at, "github_app")
            return client

    def client(self, *, require_write: bool = False):
        status = self.status()
        if status.mode == "github_app":
            return self._app_client()
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            from github import Auth, Github

            return Github(auth=Auth.Token(token))
        if require_write:
            raise PermissionError(
                "GitHub publishing is not connected. Configure a least-privilege GitHub App installation."
            )
        from github import Github

        return Github()


github_credentials = GitHubCredentialBroker()


__all__ = ["GitHubAuthStatus", "GitHubCredentialBroker", "github_credentials"]
