"""Token-free per-user session command handling."""

from __future__ import annotations

from collections.abc import Callable

from agent.orchestration.contracts import ResponseCandidate


class SessionCommandService:
    def __init__(self, session_factory: Callable):
        self._session_factory = session_factory

    def handle(self, text: str, platform: str, internal_id: str) -> ResponseCandidate | None:
        command = text.strip().lower()
        store = self._session_factory()
        if command in {"/reset", "/new"}:
            store.reset_session(platform, internal_id)
            return ResponseCandidate(
                "✅ Your conversation history has been cleared. Fresh start!",
                "system",
            )
        if command == "/history":
            count = len(store.get_history(platform, internal_id))
            return ResponseCandidate(
                f"📊 Your session: {count} messages stored.\nUse /reset to clear your history.",
                "system",
            )
        return None
