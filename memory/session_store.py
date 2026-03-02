"""
memory/session_store.py

Module-level singleton so every connector imports the same SessionManager
without having to pass it around.

Usage
-----
    from memory.session_store import get_session_manager

    sm = get_session_manager()
    history = sm.get_history("telegram", user_id)
"""

from __future__ import annotations

import os
import logging

from .session_manager import SessionManager

logger = logging.getLogger(__name__)

_instance: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Return the module-level SessionManager singleton, creating it if needed."""
    global _instance
    if _instance is None:
        mongo_uri = os.getenv("MONGODB_URI")
        if not mongo_uri:
            raise RuntimeError(
                "MONGODB_URI environment variable is required for SessionManager but was not set"
            )
        db_name   = os.environ.get("MONGODB_DB", "assistant_db")
        collection = os.environ.get("SESSION_COLLECTION", "sessions")
        _instance = SessionManager(
            mongo_uri=mongo_uri,
            db_name=db_name,
            collection_name=collection,
        )
    return _instance


def reset_session_manager() -> None:
    """Tear down and recreate the singleton (useful in tests)."""
    global _instance
    if _instance is not None:
        _instance.close()
        _instance = None
