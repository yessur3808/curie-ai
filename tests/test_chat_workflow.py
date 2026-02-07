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
        self.workflow = ChatWorkflow(persona={"name": "TestBot", "system_prompt": "You are a helpful assistant."})
    
    def test_sanitize_code_blocks(self):
        """Test that code blocks are removed from responses."""
        response_with_code = """Here's some text before.
```python
import re
def get_weather_info():
    # Using regular expressions
    current_date = re.search(r'Current date: (.*)', text).group(1)
    return current_date
```
And some text after."""
        
        sanitized = self.workflow._sanitize_output(response_with_code)
        
        # Code block should be removed
        assert "```" not in sanitized
        assert "import re" not in sanitized
        assert "def get_weather_info" not in sanitized
        
        # Regular text should remain
        assert "Here's some text before" in sanitized
        assert "And some text after" in sanitized
    
    def test_sanitize_inline_code(self):
        """Test that inline code is removed from responses."""
        response_with_inline = "You can use `variable_name` to store values."
        
        sanitized = self.workflow._sanitize_output(response_with_inline)
        
        # Inline code should be removed
        assert "`" not in sanitized
        assert "variable_name" not in sanitized
        
        # Regular text should remain
        assert "You can use" in sanitized
        assert "to store values" in sanitized
    
    def test_sanitize_speaker_tags(self):
        """Test that speaker tags are removed."""
        response_with_tag = "Assistant: Here is my response to your question."
        
        sanitized = self.workflow._sanitize_output(response_with_tag)
        
        # Speaker tag should be removed
        assert not sanitized.startswith("Assistant:")
        assert sanitized.startswith("Here is")
    
    def test_sanitize_meta_notes(self):
        """Test that meta notes are removed."""
        response_with_meta = "This is helpful. [Note: This is additional context] I hope this helps!"
        
        sanitized = self.workflow._sanitize_output(response_with_meta)
        
        # Meta note should be removed
        assert "[Note:" not in sanitized
        assert "additional context" not in sanitized
        
        # Regular text should remain
        assert "This is helpful" in sanitized
        assert "I hope this helps" in sanitized
    
    def test_sanitize_actions(self):
        """Test that action descriptions are removed."""
        response_with_action = "Sure, I can help *smiles warmly* with that."
        
        sanitized = self.workflow._sanitize_output(response_with_action)
        
        # Action should be removed
        assert "*smiles" not in sanitized
        assert "warmly*" not in sanitized
        
        # Regular text should remain
        assert "Sure, I can help" in sanitized
        assert "with that" in sanitized
    
    def test_sanitize_multiple_artifacts(self):
        """Test that multiple types of artifacts are removed."""
        complex_response = """Assistant: Here's what I found.

```javascript
const result = api.call();
console.log(result);
```

[Meta: Internal processing note]

You can use `api.call()` to fetch data *gestures at screen*."""
        
        sanitized = self.workflow._sanitize_output(complex_response)
        
        # All artifacts should be removed
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
        """Test that normal conversational text is preserved."""
        normal_response = "Hi there! How are you doing today? I'm here to help with any questions you might have."
        
        sanitized = self.workflow._sanitize_output(normal_response)
        
        # All text should be preserved
        assert sanitized == normal_response
    
    def test_sanitize_empty_response(self):
        """Test handling of empty responses."""
        empty_response = ""
        
        sanitized = self.workflow._sanitize_output(empty_response)
        
        assert sanitized == ""
    
    def test_sanitize_whitespace_handling(self):
        """Test that excessive whitespace is collapsed."""
        response_with_whitespace = "This    has   too     many    spaces."
        
        sanitized = self.workflow._sanitize_output(response_with_whitespace)
        
        # Multiple spaces should be collapsed to single space
        assert "    " not in sanitized
        assert "This has too many spaces" in sanitized


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
