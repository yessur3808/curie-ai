"""
memory/session_manager.py

Per-user session isolation for Curie AI.

Provides a SessionManager that keys all conversation history and context
by a composite session key:  <scope>:<channel>:<user_id>

Scope modes (set SESSION_SCOPE in .env):
  - "single"            : all users share one context (original Curie behaviour)
  - "per_user"          : isolated by user_id across all channels
  - "per_channel_user"  : isolated by channel + user_id  ← recommended default
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_scope() -> str:
    raw = os.getenv("SESSION_SCOPE", "per_channel_user").strip().lower()
    valid = {"single", "per_user", "per_channel_user"}
    if raw not in valid:
        logger.warning("Unknown SESSION_SCOPE=%r, falling back to 'per_channel_user'", raw)
        return "per_channel_user"
    return raw


def _get_max_history() -> int:
    try:
        return int(os.getenv("SESSION_MAX_HISTORY", "50"))
    except ValueError:
        return 50


# ---------------------------------------------------------------------------
# Session key builder
# ---------------------------------------------------------------------------

def build_session_key(channel: str, user_id: str | int) -> str:
    """
    Return a MongoDB document key for this (channel, user) combination.

    Examples
    --------
    scope=per_channel_user  →  "telegram:123456789"
    scope=per_user          →  "user:123456789"
    scope=single            →  "global:default"
    """
    scope = _get_scope()
    uid = str(user_id)
    ch  = channel.lower().strip()

    if scope == "single":
        return "global:default"
    elif scope == "per_user":
        return f"user:{uid}"
    else:  # per_channel_user (default)
        return f"{ch}:{uid}"


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Manages per-user conversation sessions backed by MongoDB.

    Each session document looks like::

        {
            "_id": "telegram:123456789",
            "channel": "telegram",
            "user_id": "123456789",
            "scope": "per_channel_user",
            "created_at": <datetime>,
            "updated_at": <datetime>,
            "messages": [
                {"role": "user",      "content": "...", "ts": <datetime>},
                {"role": "assistant", "content": "...", "ts": <datetime>},
                ...
            ],
            "metadata": {}   # arbitrary per-user store
        }
    """

    def __init__(self, mongo_uri: str, db_name: str, collection_name: str = "sessions"):
        self._client: MongoClient = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        # Force an early connection attempt so startup failures are fast and explicit.
        self._client.admin.command("ping")
        self._db = self._client[db_name]
        self._col: Collection = self._db[collection_name]
        self._max_history = _get_max_history()
        self._scope = _get_scope()
        self._ensure_indexes()
        logger.info(
            "SessionManager ready  scope=%s  max_history=%d  collection=%s",
            self._scope, self._max_history, collection_name,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_indexes(self) -> None:
        self._col.create_index([("channel", ASCENDING), ("user_id", ASCENDING)])
        self._col.create_index([("user_id", ASCENDING)])
        self._col.create_index([("updated_at", ASCENDING)])

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _session_key(self, channel: str, user_id: str | int) -> str:
        return build_session_key(channel, user_id)

    def _get_or_create(self, channel: str, user_id: str | int) -> dict:
        key = self._session_key(channel, user_id)
        now_created = self._now()
        now_updated = self._now()
        update_doc = {
            "$setOnInsert": {
                "_id": key,
                "channel": channel.lower(),
                "user_id": str(user_id),
                "scope": self._scope,
                "created_at": now_created,
                "updated_at": now_updated,
                "messages": [],
                "metadata": {},
            }
        }
        result = self._col.update_one({"_id": key}, update_doc, upsert=True)
        if result.upserted_id is not None:
            logger.debug("Created new session  key=%s", key)
        doc = self._col.find_one({"_id": key})
        return doc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_history(self, channel: str, user_id: str | int) -> list[dict]:
        """Return the conversation history for this user (newest messages last)."""
        doc = self._get_or_create(channel, user_id)
        return doc.get("messages", [])

    def add_message(
        self,
        channel: str,
        user_id: str | int,
        role: str,
        content: str,
        extra: dict | None = None,
    ) -> None:
        """
        Append one message to the session and enforce the max-history window.

        Parameters
        ----------
        channel : str   e.g. "telegram", "discord", "whatsapp"
        user_id : str   platform-specific sender identifier
        role    : str   "user" | "assistant" | "system"
        content : str   message text
        extra   : dict  optional extra fields stored on the message document
        """
        key = self._session_key(channel, user_id)
        self._get_or_create(channel, user_id)  # ensure document exists

        message: dict[str, Any] = {
            "role": role,
            "content": content,
            "ts": self._now(),
        }
        if extra:
            message.update(extra)

        # Push new message and trim to max_history in a single atomic update
        self._col.update_one(
            {"_id": key},
            {
                "$push": {
                    "messages": {
                        "$each": [message],
                        "$slice": -self._max_history,   # keep most recent N
                    }
                },
                "$set": {"updated_at": self._now()},
            },
        )

    def reset_session(self, channel: str, user_id: str | int) -> None:
        """Wipe the conversation history for this user (keeps metadata)."""
        key = self._session_key(channel, user_id)
        # Ensure the session document exists with the full schema before resetting.
        self._get_or_create(channel, user_id)
        self._col.update_one(
            {"_id": key},
            {"$set": {"messages": [], "updated_at": self._now()}},
        )
        logger.info("Session reset  key=%s", key)

    def get_metadata(self, channel: str, user_id: str | int) -> dict:
        """Return the per-user metadata dict."""
        doc = self._get_or_create(channel, user_id)
        return doc.get("metadata", {})

    def set_metadata(self, channel: str, user_id: str | int, key: str, value: Any) -> None:
        """Set a single key in the per-user metadata store."""
        session_key = self._session_key(channel, user_id)
        self._get_or_create(channel, user_id)
        self._col.update_one(
            {"_id": session_key},
            {
                "$set": {
                    f"metadata.{key}": value,
                    "updated_at": self._now(),
                }
            },
        )

    def list_sessions(self, channel: str | None = None) -> list[dict]:
        """List all sessions, optionally filtered by channel when scope is channel-specific.

        Notes
        -----
        The `channel` filter is only meaningful when the session scope includes the
        channel in its key (e.g. "per_channel_user"). For non-channel-scoped modes
        such as "per_user" or "single", the `channel` argument is ignored to avoid
        misleading results.
        """
        query: dict[str, Any] = {}
        if channel:
            if getattr(self, "_scope", None) == "per_channel_user":
                query["channel"] = channel.lower()
            else:
                logger.debug(
                    "Ignoring channel filter in list_sessions for non-channel-scoped "
                    "session scope %r",
                    getattr(self, "_scope", None),
                )
        return list(
            self._col.find(query, {"messages": 0}).sort("updated_at", -1)
        )

    def session_exists(self, channel: str, user_id: str | int) -> bool:
        key = self._session_key(channel, user_id)
        return self._col.count_documents({"_id": key}) > 0

    def reset_user_all_channels(self, user_id: str | int) -> None:
        """Wipe conversation history for a user across all channels.

        When SESSION_SCOPE is ``single`` every user shares the ``global:default``
        document, so filtering by ``user_id`` may return no matches.  In that
        mode the method falls back to clearing that shared document instead.
        """
        if self._scope == "single":
            self.reset_session("global", "default")
            logger.info("Reset shared single-scope session for user_id=%s", user_id)
            return
        uid = str(user_id)
        result = self._col.update_many(
            {"user_id": uid},
            {"$set": {"messages": [], "updated_at": self._now()}},
        )
        logger.info("Reset all sessions for user_id=%s  count=%d", uid, result.modified_count)

    def clear_all_sessions(self) -> None:
        """Wipe all conversation history across every session (admin use)."""
        result = self._col.update_many({}, {"$set": {"messages": [], "updated_at": self._now()}})
        logger.info("All sessions cleared  count=%d", result.modified_count)

    def close(self) -> None:
        self._client.close()
