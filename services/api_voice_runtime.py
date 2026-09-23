"""Thin Python bridge to Curie's native API and live-voice runtime.

The Rust extension owns mutable coordination state. Python keeps framework and
model-provider integration because FastAPI, Torch, and the model SDKs are still
Python-native dependencies.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

_NATIVE_MODULE = None
_NATIVE_IMPORT_ATTEMPTED = False
_NATIVE_IMPORT_ERROR: str | None = None
_API_COORDINATOR = None
_VOICE_SESSIONS = None
_SINGLETON_LOCK = threading.Lock()


def _mode() -> str:
    configured = os.getenv("CURIE_API_VOICE_RUNTIME", "auto").strip().casefold()
    return configured if configured in {"auto", "rust", "python"} else "auto"


def _native_module():
    global _NATIVE_MODULE, _NATIVE_IMPORT_ATTEMPTED, _NATIVE_IMPORT_ERROR
    if _NATIVE_IMPORT_ATTEMPTED:
        return _NATIVE_MODULE
    _NATIVE_IMPORT_ATTEMPTED = True
    try:
        import _curie_api_voice_runtime as native

        _NATIVE_MODULE = native
        _NATIVE_IMPORT_ERROR = None
    except (ImportError, OSError) as exc:
        _NATIVE_MODULE = None
        _NATIVE_IMPORT_ERROR = type(exc).__name__
    return _NATIVE_MODULE


def api_voice_runtime_status() -> dict[str, Any]:
    mode = _mode()
    native = _native_module()
    available = native is not None
    version = None
    if available:
        try:
            version = str(native.runtime_version())
        except Exception:
            available = False
    active = "rust" if available and mode != "python" else "python"
    return {
        "mode": mode,
        "available": available,
        "active": active,
        "version": version,
        "fallback": mode != "rust",
        "import_error": _NATIVE_IMPORT_ERROR,
    }


def required_native():
    status = api_voice_runtime_status()
    if status["mode"] == "rust" and status["active"] != "rust":
        raise RuntimeError(
            "CURIE_API_VOICE_RUNTIME=rust but the native module is unavailable"
        )
    return _native_module() if status["active"] == "rust" else None


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def api_request_coordinator():
    global _API_COORDINATOR
    native = required_native()
    if native is None:
        return None
    if _API_COORDINATOR is None:
        with _SINGLETON_LOCK:
            if _API_COORDINATOR is None:
                _API_COORDINATOR = native.ApiRequestCoordinator(
                    int(os.getenv("CURIE_API_REQUEST_CAPACITY", "64")),
                    int(os.getenv("CURIE_API_REQUESTS_PER_OWNER", "2")),
                    int(os.getenv("CURIE_API_RESPONSE_CACHE", "2048")),
                )
    return _API_COORDINATOR


def voice_session_manager():
    global _VOICE_SESSIONS
    native = required_native()
    if native is None:
        return None
    if _VOICE_SESSIONS is None:
        with _SINGLETON_LOCK:
            if _VOICE_SESSIONS is None:
                _VOICE_SESSIONS = native.VoiceSessionManager(
                    int(os.getenv("CURIE_VOICE_SESSION_CAPACITY", "128")),
                    int(os.getenv("CURIE_VOICE_HISTORY_TURNS", "4")),
                )
    return _VOICE_SESSIONS


def decode(value: str) -> Any:
    return json.loads(value)


def validate_message(
    user_id: str, message: str, idempotency_key: str | None = None
) -> dict[str, str] | None:
    native = required_native()
    if native is None:
        return None
    return decode(native.validate_api_message(user_id, message, idempotency_key))


def begin_request(request_id: str, owner_id: str, kind: str) -> dict | None:
    coordinator = api_request_coordinator()
    if coordinator is None:
        return None
    return decode(
        coordinator.begin(
            request_id,
            owner_id,
            kind,
            now_ms(),
            int(os.getenv("CURIE_API_REQUEST_LEASE_MS", "120000")),
        )
    )


def finish_request(request_id: str, token: int, response: dict) -> bool:
    coordinator = api_request_coordinator()
    if coordinator is None:
        return False
    return bool(
        coordinator.finish(
            request_id,
            token,
            json.dumps(response, ensure_ascii=False, separators=(",", ":")),
            now_ms(),
            int(os.getenv("CURIE_API_RESPONSE_TTL_MS", "600000")),
        )
    )


def fail_request(request_id: str, token: int) -> bool:
    coordinator = api_request_coordinator()
    return bool(coordinator and coordinator.fail(request_id, token))


def runtime_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {"kernel": api_voice_runtime_status()}
    coordinator = api_request_coordinator()
    sessions = voice_session_manager()
    if coordinator is not None:
        result["requests"] = decode(coordinator.snapshot(now_ms()))
    if sessions is not None:
        result["voice_sessions"] = decode(sessions.snapshot(now_ms()))
    return result


def reset_api_voice_runtime() -> None:
    global _API_COORDINATOR, _VOICE_SESSIONS
    _API_COORDINATOR = None
    _VOICE_SESSIONS = None


__all__ = [
    "api_request_coordinator",
    "api_voice_runtime_status",
    "begin_request",
    "decode",
    "fail_request",
    "finish_request",
    "now_ms",
    "required_native",
    "reset_api_voice_runtime",
    "runtime_snapshot",
    "validate_message",
    "voice_session_manager",
]
