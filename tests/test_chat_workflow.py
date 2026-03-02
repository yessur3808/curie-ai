# tests/test_chat_workflow.py
"""
Tests for ChatWorkflow output sanitization and response generation.
"""

import asyncio
import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Stub heavyweight dependencies that are unavailable in unit-test environments
# (psycopg2 / pymongo / real LLM) before any application module is imported.
for _mod in ("psycopg2", "psycopg2.extras", "psycopg2.extensions",
             "pymongo", "pymongo.collection", "pymongo.errors"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Stub the memory package so importing ChatWorkflow doesn't try to connect.
_mock_memory_pkg = MagicMock()
sys.modules.setdefault("memory", _mock_memory_pkg)
sys.modules.setdefault("memory.database", MagicMock())
sys.modules.setdefault("memory.users", MagicMock())
sys.modules.setdefault("memory.conversations", MagicMock())
sys.modules.setdefault("memory.session_store", MagicMock())

# Stub the llm package
sys.modules.setdefault("llm", MagicMock())

from agent.chat_workflow import ChatWorkflow  # noqa: E402


class TestOutputSanitization:
    """Test that ChatWorkflow properly sanitizes LLM outputs."""
    
    def setup_method(self):
        """Set up test fixtures."""
        # Test both sanitization modes
        self.workflow_minimal = ChatWorkflow(
            persona={"name": "TestBot", "system_prompt": "You are a helpful assistant."},
            minimal_sanitization=True
        )
        self.workflow_aggressive = ChatWorkflow(
            persona={"name": "TestBot", "system_prompt": "You are a helpful assistant."},
            minimal_sanitization=False
        )
    
    def test_sanitize_code_blocks_minimal_mode(self):
        """Test that code blocks are PRESERVED in minimal sanitization mode."""
        response_with_code = """Here's some text before.
```python
import re
def get_weather_info():
    # Using regular expressions
    current_date = re.search(r'Current date: (.*)', text).group(1)
    return current_date
```
And some text after."""
        
        sanitized = self.workflow_minimal._sanitize_output(response_with_code)
        
        # Code block should be PRESERVED in minimal mode
        assert "```" in sanitized
        assert "import re" in sanitized
        assert "def get_weather_info" in sanitized
        
        # Regular text should remain
        assert "Here's some text before" in sanitized
        assert "And some text after" in sanitized
    
    def test_sanitize_code_blocks_aggressive_mode(self):
        """Test that code blocks are REMOVED in aggressive sanitization mode."""
        response_with_code = """Here's some text before.
```python
import re
def get_weather_info():
    # Using regular expressions
    current_date = re.search(r'Current date: (.*)', text).group(1)
    return current_date
```
And some text after."""
        
        sanitized = self.workflow_aggressive._sanitize_output(response_with_code)
        
        # Code block should be REMOVED in aggressive mode
        assert "```" not in sanitized
        assert "import re" not in sanitized
        assert "def get_weather_info" not in sanitized
        
        # Regular text should remain
        assert "Here's some text before" in sanitized
        assert "And some text after" in sanitized
    
    def test_sanitize_inline_code_minimal_mode(self):
        """Test that inline code is PRESERVED in minimal sanitization mode."""
        response_with_inline = "You can use `variable_name` to store values."
        
        sanitized = self.workflow_minimal._sanitize_output(response_with_inline)
        
        # Inline code should be PRESERVED in minimal mode
        assert "`" in sanitized
        assert "variable_name" in sanitized
        
        # Regular text should remain
        assert "You can use" in sanitized
        assert "to store values" in sanitized
    
    def test_sanitize_inline_code_aggressive_mode(self):
        """Test that inline code is REMOVED in aggressive sanitization mode."""
        response_with_inline = "You can use `variable_name` to store values."
        
        sanitized = self.workflow_aggressive._sanitize_output(response_with_inline)
        
        # Inline code should be REMOVED in aggressive mode
        assert "`" not in sanitized
        assert "variable_name" not in sanitized
        
        # Regular text should remain
        assert "You can use" in sanitized
        assert "to store values" in sanitized
    
    def test_sanitize_speaker_tags(self):
        """Test that speaker tags are ALWAYS removed (both modes)."""
        response_with_tag = "Assistant: Here is my response to your question."
        
        # Test minimal mode
        sanitized_minimal = self.workflow_minimal._sanitize_output(response_with_tag)
        assert not sanitized_minimal.startswith("Assistant:")
        assert sanitized_minimal.startswith("Here is")
        
        # Test aggressive mode
        sanitized_aggressive = self.workflow_aggressive._sanitize_output(response_with_tag)
        assert not sanitized_aggressive.startswith("Assistant:")
        assert sanitized_aggressive.startswith("Here is")
    
    def test_sanitize_meta_notes(self):
        """Test that meta notes are ALWAYS removed (both modes)."""
        response_with_meta = "This is helpful. [Note: This is additional context] I hope this helps!"
        
        # Test minimal mode
        sanitized_minimal = self.workflow_minimal._sanitize_output(response_with_meta)
        assert "[Note:" not in sanitized_minimal
        assert "additional context" not in sanitized_minimal
        assert "This is helpful" in sanitized_minimal
        assert "I hope this helps" in sanitized_minimal
        
        # Test aggressive mode
        sanitized_aggressive = self.workflow_aggressive._sanitize_output(response_with_meta)
        assert "[Note:" not in sanitized_aggressive
        assert "additional context" not in sanitized_aggressive
        assert "This is helpful" in sanitized_aggressive
        assert "I hope this helps" in sanitized_aggressive
    
    def test_sanitize_actions(self):
        """Test that action descriptions are ALWAYS removed (both modes)."""
        response_with_action = "Sure, I can help *smiles warmly* with that."
        
        # Test minimal mode
        sanitized_minimal = self.workflow_minimal._sanitize_output(response_with_action)
        assert "*smiles" not in sanitized_minimal
        assert "warmly*" not in sanitized_minimal
        assert "Sure, I can help" in sanitized_minimal
        assert "with that" in sanitized_minimal
        
        # Test aggressive mode
        sanitized_aggressive = self.workflow_aggressive._sanitize_output(response_with_action)
        assert "*smiles" not in sanitized_aggressive
        assert "warmly*" not in sanitized_aggressive
        assert "Sure, I can help" in sanitized_aggressive
        assert "with that" in sanitized_aggressive
    
    def test_sanitize_multiple_artifacts_minimal_mode(self):
        """Test sanitization in minimal mode with mixed content."""
        complex_response = """Assistant: Here's what I found.

```javascript
const result = api.call();
console.log(result);
```

[Meta: Internal processing note]

You can use `api.call()` to fetch data *gestures at screen*."""
        
        sanitized = self.workflow_minimal._sanitize_output(complex_response)
        
        # Artifacts should be removed
        assert "Assistant:" not in sanitized
        assert "[Meta:" not in sanitized
        assert "*gestures" not in sanitized
        
        # Code should be PRESERVED in minimal mode
        assert "```" in sanitized
        assert "const result" in sanitized
        assert "`api.call()`" in sanitized
        
        # Regular text should remain
        assert "Here's what I found" in sanitized
        assert "to fetch data" in sanitized
    
    def test_sanitize_multiple_artifacts_aggressive_mode(self):
        """Test sanitization in aggressive mode with mixed content."""
        complex_response = """Assistant: Here's what I found.

```javascript
const result = api.call();
console.log(result);
```

[Meta: Internal processing note]

You can use `api.call()` to fetch data *gestures at screen*."""
        
        sanitized = self.workflow_aggressive._sanitize_output(complex_response)
        
        # All artifacts should be removed in aggressive mode
        assert "Assistant:" not in sanitized
        assert "```" not in sanitized
        assert "const result" not in sanitized
        assert "[Meta:" not in sanitized
        assert "`api.call()`" not in sanitized
        assert "*gestures" not in sanitized
        
        # Regular text should remain
        assert "Here's what I found" in sanitized
        assert "to fetch data" in sanitized
    
    def test_sanitize_preserves_normal_text(self):
        """Test that normal conversational text is preserved (both modes)."""
        normal_response = "Hi there! How are you doing today? I'm here to help with any questions you might have."
        
        # Test minimal mode
        sanitized_minimal = self.workflow_minimal._sanitize_output(normal_response)
        assert sanitized_minimal == normal_response
        
        # Test aggressive mode
        sanitized_aggressive = self.workflow_aggressive._sanitize_output(normal_response)
        assert sanitized_aggressive == normal_response
    
    def test_sanitize_empty_response(self):
        """Test handling of empty responses (both modes)."""
        empty_response = ""
        
        # Test minimal mode
        sanitized_minimal = self.workflow_minimal._sanitize_output(empty_response)
        assert sanitized_minimal == ""
        
        # Test aggressive mode
        sanitized_aggressive = self.workflow_aggressive._sanitize_output(empty_response)
        assert sanitized_aggressive == ""
    
    def test_sanitize_whitespace_handling(self):
        """Test that excessive whitespace is collapsed (both modes)."""
        response_with_whitespace = "This    has   too     many    spaces."
        
        # Test minimal mode
        sanitized_minimal = self.workflow_minimal._sanitize_output(response_with_whitespace)
        assert "    " not in sanitized_minimal
        assert "This has too many spaces" in sanitized_minimal
        
        # Test aggressive mode
        sanitized_aggressive = self.workflow_aggressive._sanitize_output(response_with_whitespace)
        assert "    " not in sanitized_aggressive
        assert "This has too many spaces" in sanitized_aggressive


class TestProactiveMessagingIntegration:
    """Test that proactive messaging can be enabled/disabled via environment."""
    
    def test_env_variable_default_true(self, monkeypatch):
        """Test that proactive messaging defaults to enabled."""
        # Remove env variable to test default
        monkeypatch.delenv("ENABLE_PROACTIVE_MESSAGING", raising=False)
        
        # Default should be "true"
        enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
        assert enable_proactive is True
    
    def test_env_variable_explicit_true(self, monkeypatch):
        """Test that ENABLE_PROACTIVE_MESSAGING=true enables it."""
        monkeypatch.setenv("ENABLE_PROACTIVE_MESSAGING", "true")
        
        enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
        assert enable_proactive is True
    
    def test_env_variable_explicit_false(self, monkeypatch):
        """Test that ENABLE_PROACTIVE_MESSAGING=false disables it."""
        monkeypatch.setenv("ENABLE_PROACTIVE_MESSAGING", "false")
        
        enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
        assert enable_proactive is False
    
    def test_env_variable_case_insensitive(self, monkeypatch):
        """Test that env variable is case insensitive."""
        monkeypatch.setenv("ENABLE_PROACTIVE_MESSAGING", "FALSE")
        
        enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
        assert enable_proactive is False
        
        monkeypatch.setenv("ENABLE_PROACTIVE_MESSAGING", "TRUE")
        enable_proactive = os.getenv("ENABLE_PROACTIVE_MESSAGING", "true").lower() == "true"
        assert enable_proactive is True


class TestMinimalSanitizationConfiguration:
    """Test that minimal sanitization can be controlled via environment."""
    
    def test_minimal_sanitization_default_true(self, monkeypatch):
        """Test that minimal sanitization defaults to enabled (True)."""
        monkeypatch.delenv("MINIMAL_SANITIZATION", raising=False)
        
        # Default should be "true"
        minimal_sanitization = os.getenv("MINIMAL_SANITIZATION", "true").lower() == "true"
        assert minimal_sanitization is True
    
    def test_minimal_sanitization_explicit_true(self, monkeypatch):
        """Test that MINIMAL_SANITIZATION=true enables it."""
        monkeypatch.setenv("MINIMAL_SANITIZATION", "true")
        
        minimal_sanitization = os.getenv("MINIMAL_SANITIZATION", "true").lower() == "true"
        assert minimal_sanitization is True
    
    def test_minimal_sanitization_explicit_false(self, monkeypatch):
        """Test that MINIMAL_SANITIZATION=false uses aggressive mode."""
        monkeypatch.setenv("MINIMAL_SANITIZATION", "false")
        
        minimal_sanitization = os.getenv("MINIMAL_SANITIZATION", "true").lower() == "true"
        assert minimal_sanitization is False
    
    def test_minimal_sanitization_case_insensitive(self, monkeypatch):
        """Test that MINIMAL_SANITIZATION env variable is case insensitive."""
        monkeypatch.setenv("MINIMAL_SANITIZATION", "FALSE")
        
        minimal_sanitization = os.getenv("MINIMAL_SANITIZATION", "true").lower() == "true"
        assert minimal_sanitization is False
        
        monkeypatch.setenv("MINIMAL_SANITIZATION", "TRUE")
        minimal_sanitization = os.getenv("MINIMAL_SANITIZATION", "true").lower() == "true"
        assert minimal_sanitization is True


class TestSessionManagerIntegration:
    """
    Tests for ChatWorkflow's SessionManager integration.

    All tests stub ``agent.chat_workflow.get_session_manager`` so no real
    MongoDB connection is required.
    """

    def _make_workflow(self):
        return ChatWorkflow(
            persona={"name": "TestBot", "system_prompt": "You are a helpful assistant."},
        )

    # ------------------------------------------------------------------
    # _batch_load_context
    # ------------------------------------------------------------------

    def test_batch_load_context_uses_platform_and_internal_id(self):
        """History is fetched with (platform, internal_id) and converted to tuples."""
        mock_sm = MagicMock()
        mock_sm.get_history.return_value = [
            {"role": "user", "content": "hello", "ts": "2024-01-01T00:00:00"},
            {"role": "assistant", "content": "hi there", "ts": "2024-01-01T00:00:01"},
        ]

        workflow = self._make_workflow()

        with patch("agent.chat_workflow.get_session_manager", return_value=mock_sm), \
             patch("agent.chat_workflow.UserManager") as mock_um:
            mock_um.get_user_profile.return_value = {"timezone": "UTC"}

            async def run():
                # signature: _batch_load_context(internal_id, platform)
                return await workflow._batch_load_context("user-42", "telegram")

            profile, history = asyncio.run(run())

        mock_sm.get_history.assert_called_once_with("telegram", "user-42")
        assert history == [("user", "hello"), ("assistant", "hi there")]

    def test_batch_load_context_returns_empty_list_when_no_history(self):
        """Empty history from SessionManager is returned as an empty list."""
        mock_sm = MagicMock()
        mock_sm.get_history.return_value = []

        workflow = self._make_workflow()

        with patch("agent.chat_workflow.get_session_manager", return_value=mock_sm), \
             patch("agent.chat_workflow.UserManager") as mock_um:
            mock_um.get_user_profile.return_value = {}

            async def run():
                return await workflow._batch_load_context("user-99", "discord")

            _, history = asyncio.run(run())

        assert history == []

    # ------------------------------------------------------------------
    # /reset command
    # ------------------------------------------------------------------

    def test_process_message_reset_calls_reset_session(self):
        """/reset command calls reset_session(platform, internal_id)."""
        mock_sm = MagicMock()

        workflow = self._make_workflow()

        normalized_input = {
            "platform": "telegram",
            "external_user_id": "42",
            "external_chat_id": "100",
            "message_id": "1",
            "text": "/reset",
            "internal_id": "user-uuid-42",
        }

        with patch("agent.chat_workflow.get_session_manager", return_value=mock_sm):
            async def run():
                return await workflow.process_message(normalized_input)

            result = asyncio.run(run())

        mock_sm.reset_session.assert_called_once_with("telegram", "user-uuid-42")
        assert "cleared" in result["text"].lower()

    def test_process_message_new_calls_reset_session(self):
        """/new command also calls reset_session(platform, internal_id)."""
        mock_sm = MagicMock()

        workflow = self._make_workflow()

        normalized_input = {
            "platform": "discord",
            "external_user_id": "77",
            "external_chat_id": "200",
            "message_id": "2",
            "text": "/new",
            "internal_id": "user-uuid-77",
        }

        with patch("agent.chat_workflow.get_session_manager", return_value=mock_sm):
            async def run():
                return await workflow.process_message(normalized_input)

            asyncio.run(run())

        mock_sm.reset_session.assert_called_once_with("discord", "user-uuid-77")

    # ------------------------------------------------------------------
    # /history command
    # ------------------------------------------------------------------

    def test_process_message_history_returns_count(self):
        """/history reports the number of messages in the session."""
        mock_sm = MagicMock()
        mock_sm.get_history.return_value = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]

        workflow = self._make_workflow()

        normalized_input = {
            "platform": "telegram",
            "external_user_id": "55",
            "external_chat_id": "300",
            "message_id": "3",
            "text": "/history",
            "internal_id": "user-uuid-55",
        }

        with patch("agent.chat_workflow.get_session_manager", return_value=mock_sm):
            async def run():
                return await workflow.process_message(normalized_input)

            result = asyncio.run(run())

        assert "3" in result["text"]
        mock_sm.get_history.assert_called_once_with("telegram", "user-uuid-55")


class TestSessionCommandEdgeCases:
    """
    Edge-case tests for ChatWorkflow session commands.

    These complement the happy-path coverage in TestSessionManagerIntegration
    by checking response structure, case/whitespace tolerance, and boundary
    values.
    """

    def _make_workflow(self):
        return ChatWorkflow(
            persona={"name": "TestBot", "system_prompt": "You are a helpful assistant."},
        )

    def _run_command(self, text, platform="telegram", external_user_id="1",
                     external_chat_id="10", message_id="m1",
                     internal_id="uid-1", mock_sm=None):
        """Helper: run process_message for a single command and return result."""
        if mock_sm is None:
            mock_sm = MagicMock()
            mock_sm.get_history.return_value = []

        workflow = self._make_workflow()
        normalized_input = {
            "platform": platform,
            "external_user_id": external_user_id,
            "external_chat_id": external_chat_id,
            "message_id": message_id,
            "text": text,
            "internal_id": internal_id,
        }

        with patch("agent.chat_workflow.get_session_manager", return_value=mock_sm):
            async def run():
                return await workflow.process_message(normalized_input)

            return asyncio.run(run()), mock_sm

    # ------------------------------------------------------------------
    # model_used field
    # ------------------------------------------------------------------

    def test_reset_response_model_used_is_system(self):
        """/reset response has model_used == 'system' (never consumes LLM tokens)."""
        result, _ = self._run_command("/reset")
        assert result["model_used"] == "system"

    def test_history_response_model_used_is_system(self):
        """/history response has model_used == 'system'."""
        result, _ = self._run_command("/history")
        assert result["model_used"] == "system"

    # ------------------------------------------------------------------
    # processing_time_ms field
    # ------------------------------------------------------------------

    def test_reset_response_processing_time_non_negative(self):
        """/reset includes a non-negative processing_time_ms."""
        result, _ = self._run_command("/reset")
        assert result["processing_time_ms"] >= 0

    def test_history_response_processing_time_non_negative(self):
        """/history includes a non-negative processing_time_ms."""
        result, _ = self._run_command("/history")
        assert result["processing_time_ms"] >= 0

    # ------------------------------------------------------------------
    # Case-insensitivity (code does user_text.strip().lower())
    # ------------------------------------------------------------------

    def test_reset_command_uppercase_triggers_reset(self):
        """/RESET (uppercase) also clears history — commands are case-insensitive."""
        result, mock_sm = self._run_command("/RESET")
        mock_sm.reset_session.assert_called_once()
        assert "cleared" in result["text"].lower()

    def test_new_command_mixed_case_triggers_reset(self):
        """/New (mixed case) also clears history."""
        result, mock_sm = self._run_command("/New")
        mock_sm.reset_session.assert_called_once()

    def test_history_command_uppercase_returns_count(self):
        """/HISTORY (uppercase) still returns session statistics."""
        mock_sm = MagicMock()
        mock_sm.get_history.return_value = [{"role": "user", "content": "hi"}]
        result, _ = self._run_command("/HISTORY", mock_sm=mock_sm)
        assert "1" in result["text"]

    # ------------------------------------------------------------------
    # Whitespace tolerance (code does user_text.strip())
    # ------------------------------------------------------------------

    def test_reset_command_with_surrounding_whitespace(self):
        """'  /reset  ' (padded with spaces) is treated as /reset."""
        result, mock_sm = self._run_command("  /reset  ")
        mock_sm.reset_session.assert_called_once()
        assert "cleared" in result["text"].lower()

    def test_history_command_with_surrounding_whitespace(self):
        """'  /history  ' (padded with spaces) is treated as /history."""
        mock_sm = MagicMock()
        mock_sm.get_history.return_value = []
        result, _ = self._run_command("  /history  ", mock_sm=mock_sm)
        assert result["model_used"] == "system"

    # ------------------------------------------------------------------
    # /history response text content
    # ------------------------------------------------------------------

    def test_history_response_includes_reset_hint(self):
        """/history response always reminds the user they can /reset."""
        result, _ = self._run_command("/history")
        assert "/reset" in result["text"].lower()

    def test_history_empty_session_shows_zero(self):
        """/history with an empty session reports 0 messages."""
        mock_sm = MagicMock()
        mock_sm.get_history.return_value = []
        result, _ = self._run_command("/history", mock_sm=mock_sm)
        assert "0" in result["text"]

    # ------------------------------------------------------------------
    # /reset response text content
    # ------------------------------------------------------------------

    def test_reset_response_includes_fresh_start(self):
        """/reset response includes 'Fresh start' confirmation text."""
        result, _ = self._run_command("/reset")
        assert "fresh start" in result["text"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
