"""
memory/session_mixin.py

Convenience mixin that any connector class can inherit from to get
per-user session methods without boilerplate.

Usage in a connector
--------------------

    from memory.session_mixin import SessionMixin

    class TelegramConnector(SessionMixin):

        async def handle_message(self, update, context):
            user_id = update.effective_user.id
            channel = "telegram"

            # Load isolated history for this user
            history = self.get_user_history(channel, user_id)

            # ... build prompt, call LLM ...
            reply = await self.ask_llm(history, user_message)

            # Save the turn
            self.save_turn(channel, user_id, user_message, reply)

            await update.message.reply_text(reply)
"""

from __future__ import annotations

from .session_store import get_session_manager


class SessionMixin:
    """
    Mixin providing per-user session helpers.

    All methods delegate to the module-level SessionManager singleton.
    """

    # ------------------------------------------------------------------
    # History access
    # ------------------------------------------------------------------

    def get_user_history(self, channel: str, user_id: str | int) -> list[dict]:
        """
        Return the message list for this user, ready to pass to the LLM.

        Each item is ``{"role": "user"|"assistant", "content": "..."}``.
        """
        sm = get_session_manager()
        messages = sm.get_history(channel, user_id)
        # Strip internal fields (ts, etc.) before passing to LLM
        return [{"role": m["role"], "content": m["content"]} for m in messages]

    def save_turn(
        self,
        channel: str,
        user_id: str | int,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Persist a full user→assistant exchange."""
        sm = get_session_manager()
        sm.add_message(channel, user_id, "user", user_message)
        sm.add_message(channel, user_id, "assistant", assistant_reply)

    def save_user_message(self, channel: str, user_id: str | int, content: str) -> None:
        get_session_manager().add_message(channel, user_id, "user", content)

    def save_assistant_message(self, channel: str, user_id: str | int, content: str) -> None:
        get_session_manager().add_message(channel, user_id, "assistant", content)

    # ------------------------------------------------------------------
    # Session control
    # ------------------------------------------------------------------

    def reset_user_session(self, channel: str, user_id: str | int) -> None:
        """Clear history for this user (e.g. on /reset or /new command)."""
        get_session_manager().reset_session(channel, user_id)

    # ------------------------------------------------------------------
    # User metadata
    # ------------------------------------------------------------------

    def get_user_meta(self, channel: str, user_id: str | int) -> dict:
        return get_session_manager().get_metadata(channel, user_id)

    def set_user_meta(self, channel: str, user_id: str | int, key: str, value) -> None:
        get_session_manager().set_metadata(channel, user_id, key, value)
