"""
Unit tests for connector helper logic.

Tests internal helpers (get_internal_id, message normalization) and
the API connector's idempotency-key validation without requiring live
bot tokens, databases, or an LLM.
"""

import ast
import sys
import os
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
    "psycopg2",
    "psycopg2.extras",
    "psycopg2.extensions",
    "pymongo",
    "pymongo.collection",
    "pymongo.errors",
    "llm",
    "llm.manager",
    "telegram",
    "telegram.ext",
    "dotenv",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub memory + related modules without unconditionally overwriting them.
# Using `if _mod not in sys.modules` avoids clobbering the real module when
# this file is collected after another test has already imported it.
for _mod in (
    "memory",
    "memory.session_store",
    "memory.database",
    "memory.users",
    "memory.conversations",
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
        """Import the production UUID validator from connectors.api."""
        if "connectors.api" in sys.modules:
            del sys.modules["connectors.api"]

        # Stub FastAPI + pydantic deps so the import succeeds without a server.
        sys.modules.setdefault("fastapi", MagicMock())
        sys.modules.setdefault("fastapi.responses", MagicMock())
        sys.modules.setdefault("pydantic", MagicMock())

        # Import the production regex so this test always validates the same
        # pattern the endpoint enforces — no duplicated pattern to drift.
        from connectors.api import _IDEMPOTENCY_KEY_RE

        self._uuid_re = _IDEMPOTENCY_KEY_RE

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

    @staticmethod
    def _load_ws_func():
        connector_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "api.py",
        )
        with open(connector_path) as fh:
            tree = ast.parse(fh.read())
        return next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "websocket_chat"
            ),
            None,
        )

    def test_websocket_uses_api_platform(self):
        """The websocket handler must set platform='api' in normalized_input."""
        ws_func = self._load_ws_func()
        assert (
            ws_func is not None
        ), "websocket_chat function not found in connectors/api.py"

        # Walk the AST looking for a Dict literal that has a "platform" key
        # whose value is the string constant "api".
        found = any(
            isinstance(node, ast.Dict)
            and any(
                isinstance(k, ast.Constant)
                and k.value == "platform"
                and isinstance(v, ast.Constant)
                and v.value == "api"
                for k, v in zip(node.keys, node.values)
            )
            for node in ast.walk(ws_func)
        )
        assert (
            found
        ), "websocket_chat must set platform='api' in normalized_input, not 'websocket'."

    def test_websocket_handler_sets_internal_id(self):
        """The websocket handler must include internal_id in normalized_input."""
        ws_func = self._load_ws_func()
        assert (
            ws_func is not None
        ), "websocket_chat function not found in connectors/api.py"

        # Walk the AST looking for a Dict literal that has an "internal_id" key.
        found = any(
            isinstance(node, ast.Dict)
            and any(
                isinstance(k, ast.Constant) and k.value == "internal_id"
                for k in node.keys
            )
            for node in ast.walk(ws_func)
        )
        assert found, "websocket_chat must include internal_id in normalized_input."


# ---------------------------------------------------------------------------
# Discord new-username format in clear_memory_command
# ---------------------------------------------------------------------------


class TestDiscordUsernameFormat:
    """Verify clear_memory_command handles both old and new Discord username styles."""

    def test_source_uses_discriminator_check(self):
        """The clear_memory_command must check discriminator before building username."""
        connector_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "discord_bot.py",
        )
        with open(connector_path) as fh:
            source = fh.read()

        # The fix uses the same pattern as handle_chat_message.
        assert 'ctx.author.discriminator == "0"' in source, (
            "clear_memory_command must handle new Discord usernames "
            "(discriminator == '0' means new-style, use name only)."
        )


# ---------------------------------------------------------------------------
# Slack connector helpers
# ---------------------------------------------------------------------------


class TestSlackGetInternalId:
    """Tests for connectors/slack.py _get_internal_id()."""

    def setup_method(self):
        # Stub optional slack_bolt so the module loads without the package.
        sys.modules.setdefault("slack_bolt", MagicMock())
        sys.modules.setdefault("slack_bolt.adapter", MagicMock())
        sys.modules.setdefault("slack_bolt.adapter.socket_mode", MagicMock())

        if "connectors.slack" in sys.modules:
            del sys.modules["connectors.slack"]

        from connectors.slack import _get_internal_id

        self._get_internal_id = _get_internal_id

    def test_calls_user_manager(self):
        with patch("connectors.slack.UserManager") as mock_um:
            mock_um.get_or_create_user_internal_id.return_value = "slack-uuid"
            result = self._get_internal_id("U12345")
        assert result == "slack-uuid"
        mock_um.get_or_create_user_internal_id.assert_called_once_with(
            channel="slack",
            external_id="U12345",
            secret_username="slack_U12345",
            updated_by="slack_bot",
        )

    def test_username_prefix(self):
        with patch("connectors.slack.UserManager") as mock_um:
            mock_um.get_or_create_user_internal_id.return_value = "x"
            self._get_internal_id("U99999")
            kwargs = mock_um.get_or_create_user_internal_id.call_args.kwargs
        assert kwargs["secret_username"] == "slack_U99999"


# ---------------------------------------------------------------------------
# Signal connector helpers
# ---------------------------------------------------------------------------


class TestSignalGetInternalId:
    """Tests for connectors/signal.py _get_internal_id()."""

    def setup_method(self):
        # Stub requests so the module loads without a live server.
        sys.modules.setdefault("requests", MagicMock())

        if "connectors.signal" in sys.modules:
            del sys.modules["connectors.signal"]

        from connectors.signal import _get_internal_id

        self._get_internal_id = _get_internal_id

    def test_calls_user_manager(self):
        with patch("connectors.signal.UserManager") as mock_um:
            mock_um.get_or_create_user_internal_id.return_value = "signal-uuid"
            result = self._get_internal_id("+1234567890")
        assert result == "signal-uuid"
        mock_um.get_or_create_user_internal_id.assert_called_once_with(
            channel="signal",
            external_id="+1234567890",
            secret_username="signal_+1234567890",
            updated_by="signal_bot",
        )


class TestSignalPollingLoop:
    """Verify the Signal polling loop source code contains exponential backoff."""

    def _load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "signal.py",
        )
        with open(path) as fh:
            return fh.read()

    def test_backoff_present(self):
        source = self._load_source()
        assert "_MAX_BACKOFF" in source, (
            "connectors/signal.py must implement exponential back-off "
            "(_MAX_BACKOFF constant) in the polling loop to avoid log spam "
            "when the signal-cli REST API is down"
        )

    def test_backoff_doubling_logic(self):
        source = self._load_source()
        assert "_backoff * 2" in source or "backoff * 2" in source, (
            "connectors/signal.py polling loop must double the back-off delay "
            "on consecutive failures"
        )


# ---------------------------------------------------------------------------
# Teams connector — platform tag
# ---------------------------------------------------------------------------


class TestTeamsConnectorPlatform:
    """Verify connectors/teams.py uses platform='teams' in normalized_input."""

    def _load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "teams.py",
        )
        with open(path) as fh:
            return fh.read()

    def test_platform_tag_is_teams(self):
        source = self._load_source()
        assert '"platform": "teams"' in source, (
            "connectors/teams.py must set platform='teams' in normalized_input"
        )

    def test_get_internal_id_uses_teams_channel(self):
        source = self._load_source()
        assert 'channel="teams"' in source, (
            "connectors/teams.py must pass channel='teams' to UserManager"
        )

    def test_bearer_auth_required(self):
        source = self._load_source()
        assert 'Authorization' in source, (
            "connectors/teams.py must check the Authorization header on incoming requests"
        )
        assert '"Bearer "' in source or "'Bearer '" in source or "Bearer " in source, (
            "connectors/teams.py must validate the Bearer token in the Authorization header"
        )

    def test_reply_status_checked(self):
        source = self._load_source()
        assert "is_success" in source or "raise_for_status" in source, (
            "connectors/teams.py must check the reply response status (is_success or raise_for_status)"
        )


# ---------------------------------------------------------------------------
# LINE connector — platform tag and signature verification
# ---------------------------------------------------------------------------


class TestLineConnectorPlatform:
    """Verify connectors/line.py uses platform='line' in normalized_input."""

    def _load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "line.py",
        )
        with open(path) as fh:
            return fh.read()

    def test_platform_tag_is_line(self):
        source = self._load_source()
        assert '"platform": "line"' in source, (
            "connectors/line.py must set platform='line' in normalized_input"
        )

    def test_signature_verification_present(self):
        source = self._load_source()
        assert "_verify_line_signature" in source, (
            "connectors/line.py must implement LINE webhook signature verification"
        )

    def test_get_internal_id_uses_line_channel(self):
        source = self._load_source()
        assert 'channel="line"' in source, (
            "connectors/line.py must pass channel='line' to UserManager"
        )

    def test_fail_closed_without_secret(self):
        source = self._load_source()
        # The webhook must not silently skip validation; it must check
        # LINE_CHANNEL_SECRET and reject requests when it is absent.
        assert "LINE_ALLOW_UNVERIFIED" in source, (
            "connectors/line.py must fail closed when LINE_CHANNEL_SECRET is not set; "
            "use LINE_ALLOW_UNVERIFIED=true to opt out for local dev"
        )

    def test_reply_status_checked(self):
        source = self._load_source()
        assert "is_success" in source or "raise_for_status" in source, (
            "connectors/line.py must check the reply response status (is_success or raise_for_status)"
        )


# ---------------------------------------------------------------------------
# KakaoTalk connector — platform tag and SkillResponse format
# ---------------------------------------------------------------------------


class TestKakaoConnectorPlatform:
    """Verify connectors/kakaotalk.py uses platform='kakaotalk' and correct response."""

    def _load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "kakaotalk.py",
        )
        with open(path) as fh:
            return fh.read()

    def test_platform_tag_is_kakaotalk(self):
        source = self._load_source()
        assert '"platform": "kakaotalk"' in source, (
            "connectors/kakaotalk.py must set platform='kakaotalk' in normalized_input"
        )

    def test_skill_response_version(self):
        source = self._load_source()
        assert '"version": "2.0"' in source, (
            "connectors/kakaotalk.py must return Kakao SkillResponse version 2.0"
        )

    def test_get_internal_id_uses_kakaotalk_channel(self):
        source = self._load_source()
        assert 'channel="kakaotalk"' in source, (
            "connectors/kakaotalk.py must pass channel='kakaotalk' to UserManager"
        )

    def test_secret_username_uses_kakaotalk_prefix(self):
        source = self._load_source()
        assert '"kakaotalk_' in source, (
            "connectors/kakaotalk.py must use 'kakaotalk_' prefix in secret_username "
            "to be consistent with the channel='kakaotalk' tag"
        )

    def test_no_bogus_bot_id_token_check(self):
        source = self._load_source()
        assert 'body.get("bot")' not in source, (
            "connectors/kakaotalk.py must not compare bot.id as a security token; "
            "bot.id is a bot identifier, not a shared secret"
        )


# ---------------------------------------------------------------------------
# WebChat UI — served at GET /
# ---------------------------------------------------------------------------


class TestWebChatUI:
    """Verify the WebChat UI endpoint exists in connectors/api.py."""

    def _load_source(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "connectors",
            "api.py",
        )
        with open(path) as fh:
            return fh.read()

    def test_webchat_route_exists(self):
        source = self._load_source()
        assert 'async def webchat_ui' in source, (
            "connectors/api.py must expose a GET / webchat_ui endpoint"
        )

    def test_webchat_html_contains_websocket_connect(self):
        source = self._load_source()
        assert "/ws/chat" in source, (
            "The WebChat UI HTML must connect to the /ws/chat WebSocket endpoint"
        )

    def test_webchat_uses_html_response(self):
        source = self._load_source()
        assert "HTMLResponse" in source, (
            "connectors/api.py must import and use HTMLResponse for the WebChat UI"
        )
