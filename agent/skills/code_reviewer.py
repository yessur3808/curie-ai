# agent/skills/code_reviewer.py

"""
Code Review Module - Generic code review functionality for multiple platforms
Supports GitHub, GitLab, Bitbucket and other Git platforms
"""

import os
import re
from typing import Any, Dict, Optional
import subprocess
import git
import llm.manager

# Maximum characters to read from a file for review (configurable)
MAX_FILE_CONTENT_LENGTH = int(os.getenv('CODE_REVIEW_MAX_CHARS', '4000'))


class CodeReviewer:
    """Generic code reviewer that can work with multiple Git platforms"""
    
    def __init__(self, model_name: Optional[str] = None):
        """
        Initialize the code reviewer
        
        Args:
            model_name: LLM model name to use for reviews (uses CODING_MODEL_NAME env if not provided)
        """
        self.model_name = model_name or os.getenv("CODING_MODEL_NAME")
        if not self.model_name:
            from agent.skills.coder import get_coding_model_name
            self.model_name = get_coding_model_name()
    
    def review_code_changes(self, diff_content: str, file_path: str = None) -> Dict[str, Any]:
        """
        Review code changes and provide feedback
        
        Args:
            diff_content: Git diff content to review
            file_path: Optional file path for context
            
        Returns:
            Dictionary with review results including:
            - suggestions: List of improvement suggestions
            - issues: List of potential issues found
            - score: Overall code quality score (0-10)
            - summary: Summary of the review
        """
        context = f"File: {file_path}\n" if file_path else ""
        prompt = (
            f"You are an expert code reviewer. Review the following code changes:\n\n"
            f"{context}"
            f"```diff\n{diff_content}\n```\n\n"
            f"Provide a structured review with:\n"
            f"1. Code quality score (0-10)\n"
            f"2. List of issues or concerns (security, bugs, performance, style)\n"
            f"3. Suggestions for improvement\n"
            f"4. Overall summary\n\n"
            f"Format your response as JSON with keys: score, issues, suggestions, summary"
        )
        
        response = llm.manager.ask_llm(prompt, model_name=self.model_name, max_tokens=1024)
        
        # Parse the response - handle both JSON and plain text
        try:
            import json
            review_data = json.loads(response)
        except:
            # Fallback to plain text parsing
            review_data = self._parse_plain_review(response)
        
        return review_data
    
    def _parse_plain_review(self, response: str) -> Dict[str, Any]:
        """Parse plain text review response into structured format"""
        lines = response.strip().split('\n')
        
        score = 7  # Default score
        issues = []
        suggestions = []
        summary = ""
        
        current_section = None
        for line in lines:
            line_lower = line.lower().strip()
            
            # Try to extract score
            if 'score' in line_lower or 'rating' in line_lower:
                score_match = re.search(r'(\d+(?:\.\d+)?)', line)
                if score_match:
                    score = float(score_match.group(1))
            
            # Identify sections
            elif 'issue' in line_lower or 'concern' in line_lower or 'problem' in line_lower:
                current_section = 'issues'
            elif 'suggest' in line_lower or 'recommend' in line_lower or 'improvement' in line_lower:
                current_section = 'suggestions'
            elif 'summary' in line_lower or 'overall' in line_lower:
                current_section = 'summary'
            
            # Collect content
            elif line.strip() and current_section:
                cleaned_line = line.strip('- *•#').strip()
                if cleaned_line:
                    if current_section == 'issues':
                        issues.append(cleaned_line)
                    elif current_section == 'suggestions':
                        suggestions.append(cleaned_line)
                    elif current_section == 'summary':
                        summary += cleaned_line + " "
        
        return {
            'score': score,
            'issues': issues if issues else ["No major issues found"],
            'suggestions': suggestions if suggestions else ["Code looks good"],
            'summary': summary.strip() if summary else "Code review completed"
        }
    
    def review_file(self, file_path: str, repo_path: str = ".") -> Dict[str, Any]:
        """
        Review an entire file
        
        Args:
            file_path: Path to the file to review
            repo_path: Path to the repository
            
        Returns:
            Review results dictionary
        """
        full_path = os.path.join(repo_path, file_path)
        
        try:
            with open(full_path, 'r') as f:
                content = f.read()
            
            # Limit content size using configurable constant
            truncated_content = content[:MAX_FILE_CONTENT_LENGTH]
            if len(content) > MAX_FILE_CONTENT_LENGTH:
                truncated_content += "\n\n... (content truncated)"
            
            prompt = (
                f"You are an expert code reviewer. Review the following file:\n\n"
                f"File: {file_path}\n\n"
                f"```\n{truncated_content}\n```\n\n"
                f"Provide feedback on:\n"
                f"1. Code quality and best practices\n"
                f"2. Potential bugs or issues\n"
                f"3. Security concerns\n"
                f"4. Performance considerations\n"
                f"5. Suggestions for improvement\n\n"
                f"Format as JSON with keys: score, issues, suggestions, summary"
            )
            
            response = llm.manager.ask_llm(prompt, model_name=self.model_name, max_tokens=1024)
            
            try:
                import json
                review_data = json.loads(response)
            except:
                review_data = self._parse_plain_review(response)
            
            return review_data
            
        except Exception as e:
            return {
                'score': 0,
                'issues': [f"Failed to review file: {str(e)}"],
                'suggestions': [],
                'summary': f"Error reviewing file: {str(e)}"
            }
    
    def review_pull_request(self, repo_path: str, base_branch: str, head_branch: str) -> Dict[str, Any]:
        """
        Review all changes in a pull request
        
        Args:
            repo_path: Path to the repository
            base_branch: Base branch name
            head_branch: Head branch name
            
        Returns:
            Comprehensive review of all changes
        """
        repo = git.Repo(repo_path)
        
        try:
            # Get diff between branches
            diff = repo.git.diff(f"{base_branch}...{head_branch}")
            
            if not diff:
                return {
                    'score': 10,
                    'issues': [],
                    'suggestions': [],
                    'summary': "No changes to review"
                }
            
            # Review the diff
            return self.review_code_changes(diff)
            
        except Exception as e:
            return {
                'score': 0,
                'issues': [f"Failed to generate diff: {str(e)}"],
                'suggestions': [],
                'summary': f"Error reviewing PR: {str(e)}"
            }
    
    def format_review_comment(self, review_data: Dict[str, Any]) -> str:
        """
        Format review data as a markdown comment
        
        Args:
            review_data: Review results dictionary
            
        Returns:
            Formatted markdown string
        """
        score = review_data.get('score', 0)
        issues = review_data.get('issues', [])
        suggestions = review_data.get('suggestions', [])
        summary = review_data.get('summary', '')
        
        # Score emoji
        if score >= 9:
            score_emoji = "🌟"
        elif score >= 7:
            score_emoji = "✅"
        elif score >= 5:
            score_emoji = "⚠️"
        else:
            score_emoji = "❌"
        
        comment = f"## {score_emoji} Code Review\n\n"
        comment += f"**Quality Score:** {score}/10\n\n"
        
        if summary:
            comment += f"### Summary\n{summary}\n\n"
        
        if issues and any(issues):
            comment += "### 🔍 Issues Found\n"
            for issue in issues:
                if issue:
                    comment += f"- {issue}\n"
            comment += "\n"
        
        if suggestions and any(suggestions):
            comment += "### 💡 Suggestions\n"
            for suggestion in suggestions:
                if suggestion:
                    comment += f"- {suggestion}\n"
            comment += "\n"
        
        comment += "\n*Review generated by Curie AI Code Reviewer*\n"
        
        return comment
