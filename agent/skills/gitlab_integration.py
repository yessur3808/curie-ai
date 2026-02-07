# agent/skills/gitlab_integration.py

"""
GitLab Integration Module - Handle GitLab API interactions for code review and MR management
"""

import os
from typing import Dict, List, Optional, Tuple
import requests
import git
from agent.skills.code_reviewer import CodeReviewer

# HTTP request timeout in seconds
HTTP_TIMEOUT = 30


class GitLabIntegration:
    """GitLab API integration for code review and merge request management"""
    
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        """
        Initialize GitLab integration
        
        Args:
            token: GitLab personal access token (uses GITLAB_TOKEN env if not provided)
            base_url: GitLab instance URL (uses GITLAB_URL env or defaults to gitlab.com)
        """
        self.token = token or os.getenv('GITLAB_TOKEN')
        if not self.token:
            raise ValueError("GitLab token not provided. Set GITLAB_TOKEN environment variable.")
        
        self.base_url = (base_url or os.getenv('GITLAB_URL', 'https://gitlab.com')).rstrip('/')
        self.api_url = f"{self.base_url}/api/v4"
        self.headers = {
            'PRIVATE-TOKEN': self.token,
            'Content-Type': 'application/json'
        }
        self.reviewer = CodeReviewer()
    
    def extract_project_path(self, repo_url: str) -> str:
        """
        Extract GitLab project path from repository URL
        
        Args:
            repo_url: Repository URL
            
        Returns:
            Project path (namespace/project)
        """
        import re
        # Handle various GitLab URL formats
        patterns = [
            r'gitlab\.com[:/]([^/]+/[^/]+?)(?:\.git)?$',
            r'([^/]+/[^/]+?)(?:\.git)?$'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, repo_url.strip())
            if match:
                return match.group(1)
        
        raise ValueError(f"Could not extract GitLab project path from URL: {repo_url}")
    
    def get_project(self, project_path: str) -> Dict:
        """
        Get project information
        
        Args:
            project_path: Project path (namespace/project)
            
        Returns:
            Project data dictionary
        """
        # URL encode the project path
        import urllib.parse
        encoded_path = urllib.parse.quote(project_path, safe='')
        
        url = f"{self.api_url}/projects/{encoded_path}"
        response = requests.get(url, headers=self.headers, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def create_merge_request(
        self,
        project_path: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str
    ) -> Dict:
        """
        Create a new merge request
        
        Args:
            project_path: Project path (namespace/project)
            source_branch: Source branch name
            target_branch: Target branch name
            title: MR title
            description: MR description
            
        Returns:
            Created merge request data
        """
        import urllib.parse
        encoded_path = urllib.parse.quote(project_path, safe='')
        
        url = f"{self.api_url}/projects/{encoded_path}/merge_requests"
        data = {
            'source_branch': source_branch,
            'target_branch': target_branch,
            'title': title,
            'description': description
        }
        
        response = requests.post(url, headers=self.headers, json=data, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def get_merge_request(self, project_path: str, mr_iid: int) -> Dict:
        """
        Get merge request information
        
        Args:
            project_path: Project path
            mr_iid: Merge request IID
            
        Returns:
            Merge request data
        """
        import urllib.parse
        encoded_path = urllib.parse.quote(project_path, safe='')
        
        url = f"{self.api_url}/projects/{encoded_path}/merge_requests/{mr_iid}"
        response = requests.get(url, headers=self.headers, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def add_mr_comment(self, project_path: str, mr_iid: int, comment: str) -> Dict:
        """
        Add a comment to a merge request
        
        Args:
            project_path: Project path
            mr_iid: Merge request IID
            comment: Comment text
            
        Returns:
            Created comment data
        """
        import urllib.parse
        encoded_path = urllib.parse.quote(project_path, safe='')
        
        url = f"{self.api_url}/projects/{encoded_path}/merge_requests/{mr_iid}/notes"
        data = {'body': comment}
        
        response = requests.post(url, headers=self.headers, json=data, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def get_mr_changes(self, project_path: str, mr_iid: int) -> Dict:
        """
        Get merge request changes (diff)
        
        Args:
            project_path: Project path
            mr_iid: Merge request IID
            
        Returns:
            Changes data including diffs
        """
        import urllib.parse
        encoded_path = urllib.parse.quote(project_path, safe='')
        
        url = f"{self.api_url}/projects/{encoded_path}/merge_requests/{mr_iid}/changes"
        response = requests.get(url, headers=self.headers, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def review_merge_request(self, project_path: str, mr_iid: int, post_comment: bool = True) -> Dict:
        """
        Review a merge request and optionally post the review as a comment
        
        Args:
            project_path: Project path
            mr_iid: Merge request IID
            post_comment: Whether to post review as comment
            
        Returns:
            Review results
        """
        # Get MR changes
        mr_data = self.get_mr_changes(project_path, mr_iid)
        
        # Combine all diffs
        all_diffs = []
        for change in mr_data.get('changes', []):
            diff = change.get('diff', '')
            if diff:
                file_path = change.get('new_path', change.get('old_path', 'unknown'))
                all_diffs.append(f"File: {file_path}\n{diff}")
        
        combined_diff = "\n\n".join(all_diffs)
        
        # Review the changes
        review_result = self.reviewer.review_code_changes(combined_diff)
        
        # Post comment if requested
        if post_comment:
            comment = self.reviewer.format_review_comment(review_result)
            self.add_mr_comment(project_path, mr_iid, comment)
        
        return review_result
    
    def create_and_review_mr(
        self,
        repo_path: str,
        project_url: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str
    ) -> Tuple[Dict, Dict]:
        """
        Create a merge request and immediately review it
        
        Args:
            repo_path: Local repository path
            project_url: GitLab project URL
            source_branch: Source branch name
            target_branch: Target branch name
            title: MR title
            description: MR description
            
        Returns:
            Tuple of (MR data, review results)
        """
        project_path = self.extract_project_path(project_url)
        
        # Create the MR
        mr_data = self.create_merge_request(
            project_path,
            source_branch,
            target_branch,
            title,
            description
        )
        
        # Review the MR
        review_result = self.review_merge_request(
            project_path,
            mr_data['iid'],
            post_comment=True
        )
        
        return mr_data, review_result


def apply_gitlab_code_change(
    goal: str,
    files_to_edit: List[str],
    repo_path: str,
    branch_name: str
) -> Tuple[str, Dict, str]:
    """
    Apply code changes and create a GitLab merge request
    Similar to GitHub version but for GitLab
    
    Args:
        goal: Code enhancement goal
        files_to_edit: List of files to edit
        repo_path: Repository path
        branch_name: Branch name
        
    Returns:
        Tuple of (branch_name, changes dict, MR URL)
    """
    from agent.skills.coder import (
        get_code_context,
        enhance_and_lint_files,
        commit_and_push,
        summarize_change_for_pr,
        get_coding_model_name
    )
    
    # Load configs
    GITLAB_TOKEN = os.getenv('GITLAB_TOKEN')
    MAIN_REPO = os.getenv("MAIN_REPO")
    CODING_MODEL_NAME = get_coding_model_name()
    target_base = os.getenv("TARGET_BRANCH", "main")
    
    if not GITLAB_TOKEN:
        raise ValueError("GITLAB_TOKEN not set in environment")
    
    # Git setup
    repo = git.Repo(repo_path)
    if branch_name in repo.heads:
        new_branch = repo.heads[branch_name]
    else:
        new_branch = repo.create_head(branch_name)
    new_branch.checkout()
    
    # Code context & enhancement
    code_context = get_code_context(files_to_edit, repo_path)
    changes, lint_results = enhance_and_lint_files(
        goal, files_to_edit, repo_path, code_context, CODING_MODEL_NAME
    )
    
    # Git commit & push
    commit_and_push(repo, branch_name, goal)
    
    # MR creation & review
    gitlab = GitLabIntegration(GITLAB_TOKEN)
    pr_title, pr_body_llm = summarize_change_for_pr(goal, changes, CODING_MODEL_NAME)
    
    mr_body = (
        pr_body_llm +
        f"\n\nFiles changed: {', '.join(files_to_edit)}\n"
        "This MR was generated by the Curie AI assistant. Please review before merging.\n"
    )
    
    mr_data, review_result = gitlab.create_and_review_mr(
        repo_path,
        MAIN_REPO,
        branch_name,
        target_base,
        pr_title,
        mr_body
    )
    
    # Add linting results as comment
    lint_comment = "### 🧹 Linting Results\n\n" + "\n".join(
        f"**{fname}:** {result}" for fname, result in lint_results.items()
    )
    project_path = gitlab.extract_project_path(MAIN_REPO)
    gitlab.add_mr_comment(project_path, mr_data['iid'], lint_comment)
    
    return branch_name, changes, mr_data['web_url']
