"""Canonical, bounded input events shared by every chat connector.

Connectors are intentionally thin.  They may supply ordinary dictionaries, but
the first workflow boundary always converts those dictionaries to an
``InboundEvent`` before identity resolution, memory access, or model work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import os
import re
from typing import Any, Mapping
import unicodedata


class InboundEventKind(str, Enum):
    MESSAGE = "message"
    EDITED_MESSAGE = "edited_message"
    CONNECTOR_COMMAND = "connector_command"


class EditedMessagePolicy(str, Enum):
    """How an edited connector event enters the turn stream.

    ``PROCESS_REVISION`` is Curie's default: the original delivery remains in
    the audit/history stream and each explicitly identified revision is
    processed once.  Connectors can choose ``IGNORE`` for transports where an
    edit event cannot be authenticated or ordered reliably.
    """

    PROCESS_REVISION = "process_revision"
    IGNORE = "ignore"


class InboundEventError(ValueError):
    """A safe validation failure raised before expensive or stateful work."""

    def __init__(self, code: str, *, quarantine: bool = False):
        super().__init__(code)
        self.code = code
        self.quarantine = quarantine


_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
_HORIZONTAL_SPACE = re.compile(r"[\t\x0b\x0c ]+")
_EXCESS_BLANK_LINES = re.compile(r"\n{4,}")
_SAFE_CONNECTOR = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def normalize_message_text(value: object) -> str:
    """Normalize parsing differences while preserving names and diacritics.

    NFC avoids compatibility folding of names.  The original connector text is
    retained separately on ``InboundEvent`` for exact references and citations.
    """

    if not isinstance(value, str):
        raise InboundEventError("text_must_be_string", quarantine=True)
    if "\x00" in value:
        raise InboundEventError("text_contains_nul", quarantine=True)
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH.sub("", text).translate(_PUNCTUATION_TRANSLATION)
    text = "\n".join(
        _HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n")
    )
    return _EXCESS_BLANK_LINES.sub("\n\n\n", text).strip()


@dataclass(frozen=True, slots=True)
class AttachmentDescriptor:
    """Transport-neutral attachment metadata; never contains file bytes."""

    attachment_id: str
    kind: str
    filename: str = ""
    media_type: str = ""
    size_bytes: int | None = None
    source: str = "connector"

    def __post_init__(self) -> None:
        if not self.attachment_id.strip():
            raise InboundEventError("attachment_missing_id", quarantine=True)
        if not self.kind.strip():
            raise InboundEventError("attachment_missing_kind", quarantine=True)
        if self.size_bytes is not None and self.size_bytes < 0:
            raise InboundEventError("attachment_invalid_size", quarantine=True)

    @classmethod
    def from_value(cls, value: object, index: int) -> "AttachmentDescriptor":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise InboundEventError("attachment_malformed", quarantine=True)
        attachment_id = str(
            value.get("attachment_id")
            or value.get("id")
            or value.get("file_unique_id")
            or f"attachment-{index + 1}"
        )
        kind = str(value.get("kind") or value.get("type") or "file")
        raw_size = value.get("size_bytes", value.get("file_size"))
        try:
            size = int(raw_size) if raw_size not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise InboundEventError("attachment_invalid_size", quarantine=True) from exc
        return cls(
            attachment_id=attachment_id,
            kind=kind,
            filename=str(value.get("filename") or value.get("file_name") or ""),
            media_type=str(value.get("media_type") or value.get("content_type") or ""),
            size_bytes=size,
            source=str(value.get("source") or "connector"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "kind": self.kind,
            "filename": self.filename,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """Validated message event with exact and parser-friendly text forms."""

    connector: str
    connector_account_id: str
    external_user_id: str
    external_chat_id: str
    message_id: str
    original_text: str
    normalized_text: str
    timestamp: datetime
    kind: InboundEventKind = InboundEventKind.MESSAGE
    edited_at: datetime | None = None
    attachments: tuple[AttachmentDescriptor, ...] = ()
    internal_id: str | None = None
    edit_policy: EditedMessagePolicy = EditedMessagePolicy.PROCESS_REVISION
    passthrough: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @staticmethod
    def _datetime(value: object, *, fallback_now: bool) -> datetime | None:
        if value is None:
            return datetime.now(timezone.utc) if fallback_now else None
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise InboundEventError("invalid_timestamp", quarantine=True) from exc
        else:
            raise InboundEventError("invalid_timestamp", quarantine=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        max_text_chars: int | None = None,
        max_text_bytes: int | None = None,
        max_attachment_bytes: int | None = None,
        max_total_attachment_bytes: int | None = None,
        max_attachments: int | None = None,
    ) -> "InboundEvent":
        if not isinstance(value, Mapping):
            raise InboundEventError("event_must_be_mapping", quarantine=True)

        connector = (
            str(value.get("platform") or value.get("connector") or "")
            .strip()
            .casefold()
        )
        if not _SAFE_CONNECTOR.fullmatch(connector):
            raise InboundEventError("invalid_connector", quarantine=True)

        user_id = str(value.get("external_user_id") or "").strip()
        chat_id = str(value.get("external_chat_id") or "").strip()
        message_id = str(value.get("message_id") or "").strip()
        connector_account_id = str(
            value.get("connector_account_id") or "default"
        ).strip()
        connector_account_id = connector_account_id or "default"
        if not user_id or not chat_id or not message_id:
            raise InboundEventError("missing_event_identity", quarantine=True)
        if (
            max(len(user_id), len(chat_id), len(message_id), len(connector_account_id))
            > 512
        ):
            raise InboundEventError("event_identity_too_large", quarantine=True)

        original_text = value.get("text", "")
        normalized_text = normalize_message_text(original_text)
        attachment_values = value.get("attachments") or ()
        if isinstance(attachment_values, (str, bytes, Mapping)):
            attachment_values = (attachment_values,)
        try:
            attachments = tuple(
                AttachmentDescriptor.from_value(item, index)
                for index, item in enumerate(attachment_values)
            )
        except TypeError as exc:
            raise InboundEventError("attachments_malformed", quarantine=True) from exc

        max_chars = max_text_chars or int(os.getenv("CURIE_MAX_MESSAGE_CHARS", "32000"))
        max_bytes = max_text_bytes or int(
            os.getenv("CURIE_MAX_MESSAGE_BYTES", "131072")
        )
        attachment_limit = max_attachments or int(
            os.getenv("CURIE_MAX_ATTACHMENTS", "12")
        )
        attachment_bytes = max_attachment_bytes or int(
            os.getenv("CURIE_MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024))
        )
        total_attachment_bytes = max_total_attachment_bytes or int(
            os.getenv("CURIE_MAX_TOTAL_ATTACHMENT_BYTES", str(50 * 1024 * 1024))
        )
        if (
            len(original_text) > max_chars
            or len(original_text.encode("utf-8")) > max_bytes
        ):
            raise InboundEventError("message_too_large", quarantine=True)
        if len(attachments) > attachment_limit:
            raise InboundEventError("too_many_attachments", quarantine=True)
        known_sizes = [item.size_bytes or 0 for item in attachments]
        if any(size > attachment_bytes for size in known_sizes):
            raise InboundEventError("attachment_too_large", quarantine=True)
        if sum(known_sizes) > total_attachment_bytes:
            raise InboundEventError("attachments_too_large", quarantine=True)
        if not normalized_text and not attachments:
            raise InboundEventError("empty_message")
        if not normalized_text:
            normalized_text = "Please examine the attached item."

        try:
            kind = InboundEventKind(str(value.get("event_kind") or "message"))
            edit_policy = EditedMessagePolicy(
                str(value.get("edited_message_policy") or "process_revision")
            )
        except ValueError as exc:
            raise InboundEventError("invalid_event_policy", quarantine=True) from exc
        edited_at = cls._datetime(value.get("edited_at"), fallback_now=False)
        if kind is InboundEventKind.EDITED_MESSAGE:
            if edit_policy is EditedMessagePolicy.IGNORE:
                raise InboundEventError("edited_message_ignored")
            if edited_at is None:
                raise InboundEventError(
                    "edited_message_missing_revision", quarantine=True
                )

        passthrough = {
            str(key): item
            for key, item in value.items()
            if key
            not in {
                "platform",
                "connector",
                "connector_account_id",
                "external_user_id",
                "external_chat_id",
                "message_id",
                "text",
                "timestamp",
                "event_kind",
                "edited_at",
                "edited_message_policy",
                "attachments",
                "internal_id",
            }
        }
        return cls(
            connector=connector,
            connector_account_id=connector_account_id,
            external_user_id=user_id,
            external_chat_id=chat_id,
            message_id=message_id,
            original_text=original_text,
            normalized_text=normalized_text,
            timestamp=cls._datetime(value.get("timestamp"), fallback_now=True)
            or datetime.now(timezone.utc),
            kind=kind,
            edited_at=edited_at,
            attachments=attachments,
            internal_id=(
                str(value["internal_id"]) if value.get("internal_id") else None
            ),
            edit_policy=edit_policy,
            passthrough=passthrough,
        )

    @property
    def dedupe_key(self) -> str:
        revision = (
            self.edited_at.isoformat()
            if self.kind is InboundEventKind.EDITED_MESSAGE and self.edited_at
            else "original"
        )
        raw = "\0".join(
            (
                self.connector,
                self.connector_account_id,
                self.external_chat_id,
                self.message_id,
                revision,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def as_workflow_input(self) -> dict[str, Any]:
        payload = dict(self.passthrough)
        payload.update(
            {
                "platform": self.connector,
                "connector_account_id": self.connector_account_id,
                "external_user_id": self.external_user_id,
                "external_chat_id": self.external_chat_id,
                "message_id": self.message_id,
                "text": self.normalized_text,
                "timestamp": self.timestamp,
                "event_kind": self.kind.value,
                "attachments": [item.as_dict() for item in self.attachments],
                "_original_text": self.original_text,
                "_inbound_event": self,
                "_dedupe_key": self.dedupe_key,
            }
        )
        if self.internal_id:
            payload["internal_id"] = self.internal_id
        if self.edited_at:
            payload["edited_at"] = self.edited_at
        return payload
