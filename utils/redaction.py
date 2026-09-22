"""Central, recursive credential redaction for logs, traces, and artifacts."""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
_NON_SECRET_TELEMETRY_FIELDS = frozenset(
    {
        "first_token",
        "first_token_ms",
        "output_tokens",
        "output_tokens_estimate",
        "prompt_tokens",
        "prompt_tokens_estimate",
        "token_counts",
        "tokens_per_second",
        "tokens_per_second_estimate",
    }
)
_SENSITIVE_FIELD = re.compile(
    r"(?:^|[_-])(?:token|secret|password|passwd|passcode|credential|"
    r"authorization|cookie|code_verifier|private_key|database_url)$|"
    r"(?:^|[_-])(?:api|private)[_-]?key$",
    re.I,
)
_SENSITIVE_QUERY = re.compile(
    r"^(?:access_token|refresh_token|token|code|state|client_secret|"
    r"code_verifier|key|api_key|password|signature|sig)$",
    re.I,
)
_TELEGRAM_TOKEN = re.compile(r"\b\d{6,14}:[A-Za-z0-9_-]{20,}\b")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_HEADER_SECRET = re.compile(
    r"(?im)\b(authorization|proxy-authorization|x-api-key|api-key|cookie|"
    r"set-cookie)\s*:\s*[^\r\n]+"
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(token|secret|password|passwd|passcode|api[_-]?key|client_secret|"
    r"refresh_token|access_token)\s*[:=]\s*[^\s,;&]+"
)
_URL = re.compile(
    r"\b(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s<>\"']+",
    re.I,
)


def _is_sensitive_field(key: str) -> bool:
    normalized = str(key).strip().casefold().replace("-", "_")
    return (
        normalized not in _NON_SECRET_TELEMETRY_FIELDS
        and _SENSITIVE_FIELD.search(normalized) is not None
    )


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return REDACTED
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = hostname + port
    if parsed.username is not None or parsed.password is not None:
        netloc = f"{REDACTED}@{netloc}"
    path = re.sub(
        r"(?i)(/bot)\d{6,14}:[A-Za-z0-9_-]{20,}",
        rf"\1{REDACTED}",
        parsed.path,
    )
    query = urlencode(
        [
            (key, REDACTED if _SENSITIVE_QUERY.fullmatch(key) else item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    fragment = REDACTED if parsed.fragment else ""
    return urlunsplit((parsed.scheme, netloc, path, query, fragment))


def redact_text(value: str) -> str:
    """Remove credential shapes while retaining enough structure to diagnose."""
    text = str(value)
    text = _URL.sub(lambda match: _redact_url(match.group(0)), text)
    text = _HEADER_SECRET.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _TELEGRAM_TOKEN.sub(REDACTED, text)
    text = _ASSIGNED_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    return text


def redact_secrets(value: Any, key: str = "") -> Any:
    """Recursively redact sensitive fields and token-shaped string values."""
    if key and _is_sensitive_field(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_secrets(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def secret_markers(value: Any) -> tuple[str, ...]:
    """Return detector labels used by tests and offline artifact scanning."""
    if isinstance(value, Mapping):
        markers: set[str] = set()
        for key, item in value.items():
            if _is_sensitive_field(str(key)) and str(item) != REDACTED:
                markers.add("structured_secret")
            markers.update(secret_markers(item))
        return tuple(sorted(markers))
    if isinstance(value, (list, tuple, set, frozenset)):
        markers = {marker for item in value for marker in secret_markers(item)}
        return tuple(sorted(markers))
    text = str(value)
    markers = []
    if _TELEGRAM_TOKEN.search(text):
        markers.append("telegram_bot_token")
    header_matches = [
        match.group(0)
        for pattern in (_BEARER, _HEADER_SECRET)
        for match in pattern.finditer(text)
        if REDACTED not in match.group(0)
    ]
    if header_matches:
        markers.append("provider_header")
    if any(REDACTED not in match.group(0) for match in _ASSIGNED_SECRET.finditer(text)):
        markers.append("assigned_secret")
    for match in _URL.finditer(text):
        parsed = urlsplit(match.group(0))
        if (
            parsed.username is not None
            and parsed.username.casefold() not in {"redacted", "[redacted]"}
        ) or parsed.password is not None:
            markers.append("url_credentials")
        if any(
            _SENSITIVE_QUERY.fullmatch(key) and item != REDACTED
            for key, item in parse_qsl(parsed.query)
        ):
            markers.append("sensitive_query")
    return tuple(sorted(set(markers)))


__all__ = ["REDACTED", "redact_secrets", "redact_text", "secret_markers"]
