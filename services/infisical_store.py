"""Private Infisical transport; application secrets never fall back to disk."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

BASE_URL = "http://127.0.0.1:18784"
MAX_RESPONSE = 2 * 1024 * 1024


class VaultUnavailable(RuntimeError):
    def __init__(self, status: int | None = None):
        self.status = status
        super().__init__(
            "Vault request failed" + (f" (HTTP {status})" if status else "")
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise VaultUnavailable()


def private_config(path: str | Path) -> dict:
    path = Path(path).expanduser()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise VaultUnavailable()
        try:
            result = json.load(stream)
        except (ValueError, TypeError):
            raise VaultUnavailable() from None
    if not isinstance(result, dict):
        raise VaultUnavailable()
    return result


class InfisicalStore:
    def __init__(self, config: dict, *, opener=None):
        self.config = config
        self.path = config.get("secretPath", "/curie")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise VaultUnavailable()
        if ".." in self.path.split("/") or not config.get("projectId"):
            raise VaultUnavailable()
        self.opener = opener or build_opener(ProxyHandler({}), _NoRedirect())
        self._token = config.get("serviceToken")
        self._expires = float("inf") if self._token else 0.0
        self._lock = threading.RLock()

    @classmethod
    def from_file(cls, path):
        return cls(private_config(path))

    def _raw(self, method, path, body=None, *, token=None):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            BASE_URL + path,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise VaultUnavailable()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise VaultUnavailable()
            return result
        except HTTPError as error:
            status = error.code
            error.close()
            raise VaultUnavailable(status) from None
        except (URLError, OSError, ValueError, TypeError):
            raise VaultUnavailable() from None

    def _authenticate(self):
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return
            client_id = self.config.get("clientId")
            client_secret = self.config.get("clientSecret")
            if not client_id or not client_secret:
                raise VaultUnavailable()
            result = self._raw(
                "POST",
                "/api/v1/auth/universal-auth/login",
                {"clientId": client_id, "clientSecret": client_secret},
            )
            try:
                token = result["accessToken"]
                ttl = float(result["expiresIn"])
                if not isinstance(token, str) or not token or ttl <= 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise VaultUnavailable() from None
            self._token = token
            self._expires = time.monotonic() + max(0, ttl - min(30, ttl / 2))

    def request(self, method, path, body=None):
        self._authenticate()
        try:
            return self._raw(method, path, body, token=self._token)
        except VaultUnavailable as error:
            if error.status != 401 or self.config.get("serviceToken"):
                raise
            self._expires = 0
            self._authenticate()
            return self._raw(method, path, body, token=self._token)

    def _scope(self):
        return {
            "projectId": self.config["projectId"],
            "environment": self.config.get("environment", "prod"),
            "secretPath": self.path,
        }

    def get(self, key: str) -> str | None:
        query = urlencode(
            {
                **self._scope(),
                "viewSecretValue": "true",  # pragma: allowlist secret
                "expandSecretReferences": "false",  # pragma: allowlist secret
                "includeImports": "false",
            }
        )
        try:
            result = self.request(
                "GET", "/api/v4/secrets/" + quote(key, safe="") + "?" + query
            )
        except VaultUnavailable as error:
            if error.status == 404:
                return None
            raise
        secret = result.get("secret", {})
        value = secret.get("secretValue")
        if secret.get("secretValueHidden") or not isinstance(value, str):
            raise VaultUnavailable()
        return value

    def set(self, key: str, value: str):
        endpoint = "/api/v4/secrets/" + quote(key, safe="")
        body = {
            **self._scope(),
            "secretValue": value,
            "type": "shared",
            "skipMultilineEncoding": True,
        }
        try:
            result = self.request("PATCH", endpoint, body)
        except VaultUnavailable as error:
            if error.status != 404:
                raise
            try:
                result = self.request("POST", endpoint, body)
            except VaultUnavailable as conflict:
                if conflict.status not in (400, 409) or self.get(key) is None:
                    raise
                result = self.request("PATCH", endpoint, body)
        if "approval" in result:
            raise VaultUnavailable()

    def delete(self, key: str) -> bool:
        try:
            result = self.request(
                "DELETE", "/api/v4/secrets/" + quote(key, safe=""), self._scope()
            )
        except VaultUnavailable as error:
            if error.status == 404:
                return False
            raise
        if "approval" in result:
            raise VaultUnavailable()
        return True
