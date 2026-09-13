"""Small encrypted credential vault for OAuth tokens on headless hosts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_LOCK = threading.RLock()


def _path() -> Path:
    return Path(os.getenv("CURIE_CREDENTIAL_STORE", ".curie_credentials.enc")).resolve()


def _cipher() -> Fernet:
    raw = os.getenv("CURIE_CREDENTIAL_KEY", "").strip().encode()
    if not raw:
        raise RuntimeError(
            "CURIE_CREDENTIAL_KEY is not configured; generate a Fernet key and store it only in Curie's private environment"
        )
    try:
        return Fernet(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("CURIE_CREDENTIAL_KEY is not a valid Fernet key") from exc


def _record_key(owner_id: str, provider: str) -> str:
    return hashlib.sha256(f"{owner_id}:{provider}".encode()).hexdigest()


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise PermissionError("Credential store permissions must be 0600")
    try:
        value = json.loads(_cipher().decrypt(path.read_bytes()))
    except (InvalidToken, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Credential store cannot be decrypted with the configured key"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Credential store has an invalid format")
    return value


def _write(documents: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encrypted = _cipher().encrypt(
        json.dumps(documents, separators=(",", ":"), default=str).encode()
    )
    fd, temporary = tempfile.mkstemp(prefix=".curie-vault-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def put_credential(owner_id: str, provider: str, value: dict[str, Any]) -> None:
    with _LOCK:
        documents = _load()
        documents[_record_key(owner_id, provider)] = dict(value)
        _write(documents)


def get_credential(owner_id: str, provider: str) -> dict[str, Any] | None:
    with _LOCK:
        value = _load().get(_record_key(owner_id, provider))
    return dict(value) if isinstance(value, dict) else None


def delete_credential(owner_id: str, provider: str) -> bool:
    with _LOCK:
        documents = _load()
        removed = documents.pop(_record_key(owner_id, provider), None) is not None
        if removed:
            _write(documents)
    return removed


def credential_configured(owner_id: str, provider: str) -> bool:
    try:
        return get_credential(owner_id, provider) is not None
    except (RuntimeError, PermissionError):
        return False
