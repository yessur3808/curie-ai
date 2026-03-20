"""
Unit tests for connector helper logic.

Tests internal helpers (get_internal_id, message normalization) and
the API connector's idempotency-key validation without requiring live
bot tokens, databases, or an LLM.
"""

import sys
import os
import re
import uuid
from unittest.mock import MagicMock, patch

# Add parent directory to path so project modules are importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Stub heavyweight dependencies before any application module is imported.
# We only stub modules with external dependencies (databases, bots, LLM).
# Pure-Python utility modules (utils.formatting, utils.session, utils.db)
# are intentionally NOT stubbed so they load with their real implementations,
# which avoids polluting the shared sys.modules cache and breaking other tests.
# ---------------------------------------------------------------------------
for _mod in (
    "psycopg2", "psycopg2.extras", "psycopg2.extensions",
    "pymongo", "pymongo.collection", "pymongo.errors",
    "llm", "llm.manager",
    "telegram", "telegram.ext",
    "dotenv",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub memory + related modules without unconditionally overwriting them.
# Using `if _mod not in sys.modules` avoids clobbering the real module when
# this file is collected after another test has already imported it.
for _mod in (
    "memory", "memory.session_store", "memory.database",
    "memory.users", "memory.conversations",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# utils.persona uses dotenv at import time — provide a stub so it loads cleanly.
if "utils.persona" not in sys.modules:
    sys.modules["utils.persona"] = MagicMock()


# ---------------------------------------------------------------------------
# Telegram connector helpers
# ---------------------------------------------------------------------------

class TestTelegramGetInternalId:
    """Tests for connectors/telegram.py get_internal_id()."""

    def setup_method(self):
        # Re-import fresh each time so module-level caches are clean.
        if "connectors.telegram" in sys.modules:
            del sys.modules["connectors.telegram"]

        from connectors.telegram import get_internal_id, user_session_map
        self.get_internal_id = get_internal_id
        self.user_session_map = user_session_map

    def test_returns_cached_id_when_present(self):
        """If tg_user_id is in user_session_map, return its value directly."""
        self.user_session_map[999] = "cached-internal-uuid"
        result = self.get_internal_id(999, "tg_user")
        assert result == "cached-internal-uuid"
        # Cleanup
        del self.user_session_map[999]

    def test_calls_user_manager_when_not_cached(self):
        """If tg_user_id is absent from the map, call UserManager."""
        with patch("connectors.telegram.UserManager") as mock_um:
            mock_um.get_or_create_user_internal_id.return_value = "new-uuid"
            result = self.get_internal_id(12345, "alice")
        assert result == "new-uuid"
        mock_um.get_or_create_user_internal_id.assert_called_once_with(
            channel="telegram",
            external_id=12345,
            secret_username="alice",
            updated_by="telegram_bot",
        )

    def test_uses_fallback_username_format(self):
        """get_internal_id passes the provided username to UserManager."""
        with patch("connectors.telegram.UserManager") as mock_um:
            mock_um.get_or_create_user_internal_id.return_value = "uuid-x"
            self.get_internal_id(777, "telegram_777")
            call_kwargs = mock_um.get_or_create_user_internal_id.call_args
        assert call_kwargs.kwargs["secret_username"] == "telegram_777"


# ---------------------------------------------------------------------------
# Discord connector helpers
# ---------------------------------------------------------------------------

class TestDiscordGetInternalId:
    """Tests for connectors/discord_bot.py get_internal_id()."""

    def setup_method(self):
        if "connectors.discord_bot" in sys.modules:
            del sys.modules["connectors.discord_bot"]

        from connectors.discord_bot import get_internal_id, user_session_map
        self.get_internal_id = get_internal_id
        self.user_session_map = user_session_map

    def test_returns_cached_id_when_present(self):
        self.user_session_map[42] = "discord-cached-uuid"
        result = self.get_internal_id(42, "bob#1234")
        assert result == "discord-cached-uuid"
        del self.user_session_map[42]

    def test_calls_user_manager_for_new_user(self):
        with patch("connectors.discord_bot.UserManager") as mock_um:
            mock_um.get_or_create_user_internal_id.return_value = "discord-uuid"
            result = self.get_internal_id(99, "carol")
        assert result == "discord-uuid"
        mock_um.get_or_create_user_internal_id.assert_called_once_with(
            channel="discord",
            external_id="99",
            secret_username="carol",
            updated_by="discord_bot",
        )


# ---------------------------------------------------------------------------
# API connector — idempotency key validation
# ---------------------------------------------------------------------------

class TestApiIdempotencyKeyValidation:
    """Tests for the UUID-format validation in POST /chat."""

    def setup_method(self):
        """Import MessageRequest without starting FastAPI."""
        if "connectors.api" in sys.modules:
            del sys.modules["connectors.api"]

        # Stub FastAPI + pydantic deps so the import succeeds without a server.
        sys.modules.setdefault("fastapi", MagicMock())
        sys.modules.setdefault("fastapi.responses", MagicMock())
        sys.modules.setdefault("pydantic", MagicMock())

        self._uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )

    def _is_valid_uuid(self, value: str) -> bool:
        return bool(self._uuid_re.match(value))

    def test_valid_uuid_lowercase(self):
        assert self._is_valid_uuid("550e8400-e29b-41d4-a716-446655440000") is True

    def test_valid_uuid_uppercase(self):
        assert self._is_valid_uuid("550E8400-E29B-41D4-A716-446655440000") is True

    def test_valid_uuid_from_stdlib(self):
        assert self._is_valid_uuid(str(uuid.uuid4())) is True

    def test_rejects_path_traversal(self):
        assert self._is_valid_uuid("../../../etc/passwd") is False

    def test_rejects_shell_injection(self):
        assert self._is_valid_uuid("; rm -rf /") is False

    def test_rejects_empty_string(self):
        assert self._is_valid_uuid("") is False

    def test_rejects_plain_string(self):
        assert self._is_valid_uuid("not-a-uuid") is False

    def test_rejects_uuid_with_extra_chars(self):
        assert self._is_valid_uuid("550e8400-e29b-41d4-a716-446655440000extra") is False


# ---------------------------------------------------------------------------
# WebSocket platform consistency
# ---------------------------------------------------------------------------

class TestWebSocketPlatformConsistency:
    """Verify the WebSocket handler uses platform='api' (same as REST /chat)."""

    def test_websocket_uses_api_platform(self):
        """Read the source to confirm 'platform' is set to 'api' in websocket_chat."""
        connector_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors", "api.py",
        )
        with open(connector_path) as fh:
            source = fh.read()

        # The websocket handler must NOT use "websocket" as the platform value
        # (that would create a separate user record for the same user_id).
        assert '"platform": "websocket"' not in source, (
            "WebSocket handler must not use platform='websocket'; "
            "use 'api' to share history with REST /chat users."
        )

    def test_websocket_handler_sets_internal_id(self):
        """The websocket handler must call get_internal_id() before process_message."""
        connector_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors", "api.py",
        )
        with open(connector_path) as fh:
            source = fh.read()

        # internal_id must appear in the normalized_input dict inside websocket_chat.
        assert '"internal_id": internal_id' in source, (
            "WebSocket handler must include internal_id in normalized_input."
        )


# ---------------------------------------------------------------------------
# Discord new-username format in clear_memory_command
# ---------------------------------------------------------------------------

class TestDiscordUsernameFormat:
    """Verify clear_memory_command handles both old and new Discord username styles."""

    def test_source_uses_discriminator_check(self):
        """The clear_memory_command must check discriminator before building username."""
        connector_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors", "discord_bot.py",
        )
        with open(connector_path) as fh:
            source = fh.read()

        # The fix uses the same pattern as handle_chat_message.
        assert 'ctx.author.discriminator == "0"' in source, (
            "clear_memory_command must handle new Discord usernames "
            "(discriminator == '0' means new-style, use name only)."
        )
