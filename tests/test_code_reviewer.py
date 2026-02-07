# tests/test_code_reviewer.py

"""
Tests for the Code Reviewer module
"""

import pytest
from agent.skills.code_reviewer import CodeReviewer


class TestCodeReviewer:
    """Test cases for CodeReviewer class"""
    
    @pytest.fixture
    def reviewer(self):
        """Create a CodeReviewer instance for testing"""
        # Skip if no model available
        try:
            return CodeReviewer()
        except:
            pytest.skip("LLM model not available")
    
    def test_review_code_changes_structure(self, reviewer):
        """Test that review_code_changes returns proper structure"""
        diff = """
diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,3 +1,5 @@
 def hello():
-    print("Hello")
+    name = input("Name: ")
+    print("Hello " + name)
"""
        result = reviewer.review_code_changes(diff, "test.py")
        
        # Check structure
        assert isinstance(result, dict)
        assert 'score' in result
        assert 'issues' in result
        assert 'suggestions' in result
        assert 'summary' in result
        
        # Check types
        assert isinstance(result['score'], (int, float))
        assert isinstance(result['issues'], list)
        assert isinstance(result['suggestions'], list)
        assert isinstance(result['summary'], str)
    
    def test_parse_plain_review(self, reviewer):
        """Test parsing of plain text review responses"""
        plain_text = """
        Code Quality Score: 7/10
        
        Issues:
        - Missing error handling
        - No input validation
        
        Suggestions:
        - Add try-except blocks
        - Validate user input
        
        Summary: Code is functional but needs error handling improvements
        """
        
        result = reviewer._parse_plain_review(plain_text)
        
        assert isinstance(result, dict)
        assert result['score'] == 7
        assert len(result['issues']) >= 1
        assert len(result['suggestions']) >= 1
        assert len(result['summary']) > 0
    
    def test_format_review_comment(self, reviewer):
        """Test formatting of review results into markdown"""
        review_data = {
            'score': 8,
            'issues': ['Missing docstring', 'Potential null pointer'],
            'suggestions': ['Add type hints', 'Use constants'],
            'summary': 'Good code with minor improvements needed'
        }
        
        comment = reviewer.format_review_comment(review_data)
        
        # Check markdown structure
        assert '##' in comment
        assert 'Code Review' in comment
        assert '8/10' in comment
        assert 'Issues Found' in comment
        assert 'Suggestions' in comment
        assert 'Missing docstring' in comment
        assert 'Add type hints' in comment
    
    def test_format_review_comment_score_emoji(self, reviewer):
        """Test that different scores get different emojis"""
        high_score = reviewer.format_review_comment({'score': 9.5, 'issues': [], 'suggestions': [], 'summary': ''})
        assert '🌟' in high_score
        
        good_score = reviewer.format_review_comment({'score': 7.5, 'issues': [], 'suggestions': [], 'summary': ''})
        assert '✅' in good_score
        
        medium_score = reviewer.format_review_comment({'score': 5.5, 'issues': [], 'suggestions': [], 'summary': ''})
        assert '⚠️' in medium_score
        
        low_score = reviewer.format_review_comment({'score': 3, 'issues': [], 'suggestions': [], 'summary': ''})
        assert '❌' in low_score


class TestCodeReviewerIntegration:
    """Integration tests for CodeReviewer (require actual files)"""
    
    @pytest.fixture
    def reviewer(self):
        """Create a CodeReviewer instance for testing"""
        try:
            return CodeReviewer()
        except:
            pytest.skip("LLM model not available")
    
    def test_review_file_error_handling(self, reviewer):
        """Test that review_file handles missing files gracefully"""
        result = reviewer.review_file('nonexistent_file.py', '.')
        
        assert isinstance(result, dict)
        assert result['score'] == 0
        assert len(result['issues']) > 0
        assert 'Failed to review file' in result['issues'][0]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
