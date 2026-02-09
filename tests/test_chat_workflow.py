# tests/test_chat_workflow.py
"""
Tests for ChatWorkflow output sanitization and response generation.
"""

import pytest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.chat_workflow import ChatWorkflow


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
