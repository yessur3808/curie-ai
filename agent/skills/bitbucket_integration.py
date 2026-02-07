# agent/skills/bitbucket_integration.py

"""
Bitbucket Integration Module - Handle Bitbucket API interactions for code review and PR management
"""

import os
from typing import Dict, List, Optional, Tuple
import requests
import git
from agent.skills.code_reviewer import CodeReviewer

# HTTP request timeout in seconds
HTTP_TIMEOUT = 30


class BitbucketIntegration:
    """Bitbucket API integration for code review and pull request management"""
    
    def __init__(self, username: Optional[str] = None, app_password: Optional[str] = None):
        """
        Initialize Bitbucket integration
        
        Args:
            username: Bitbucket username (uses BITBUCKET_USERNAME env if not provided)
            app_password: Bitbucket app password (uses BITBUCKET_APP_PASSWORD env if not provided)
        """
        self.username = username or os.getenv('BITBUCKET_USERNAME')
        self.app_password = app_password or os.getenv('BITBUCKET_APP_PASSWORD')
        
        if not self.username or not self.app_password:
            raise ValueError(
                "Bitbucket credentials not provided. "
                "Set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD environment variables."
            )
        
        self.api_url = "https://api.bitbucket.org/2.0"
        self.auth = (self.username, self.app_password)
        self.reviewer = CodeReviewer()
    
    def extract_workspace_repo(self, repo_url: str) -> Tuple[str, str]:
        """
        Extract workspace and repository slug from repository URL
        
        Args:
            repo_url: Repository URL
            
        Returns:
            Tuple of (workspace, repo_slug)
        """
        import re
        # Handle various Bitbucket URL formats
        patterns = [
            r'bitbucket\.org[:/]([^/]+)/([^/]+?)(?:\.git)?$',
            r'([^/]+)/([^/]+?)(?:\.git)?$'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, repo_url.strip())
            if match:
                return match.group(1), match.group(2)
        
        raise ValueError(f"Could not extract workspace/repo from URL: {repo_url}")
    
    def get_repository(self, workspace: str, repo_slug: str) -> Dict:
        """
        Get repository information
        
        Args:
            workspace: Workspace name
            repo_slug: Repository slug
            
        Returns:
            Repository data dictionary
        """
        url = f"{self.api_url}/repositories/{workspace}/{repo_slug}"
        response = requests.get(url, auth=self.auth, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def create_pull_request(
        self,
        workspace: str,
        repo_slug: str,
        source_branch: str,
        destination_branch: str,
        title: str,
        description: str
    ) -> Dict:
        """
        Create a new pull request
        
        Args:
            workspace: Workspace name
            repo_slug: Repository slug
            source_branch: Source branch name
            destination_branch: Destination branch name
            title: PR title
            description: PR description
            
        Returns:
            Created pull request data
        """
        url = f"{self.api_url}/repositories/{workspace}/{repo_slug}/pullrequests"
        data = {
            'title': title,
            'description': description,
            'source': {
                'branch': {
                    'name': source_branch
                }
            },
            'destination': {
                'branch': {
                    'name': destination_branch
                }
            }
        }
        
        response = requests.post(url, auth=self.auth, json=data, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def get_pull_request(self, workspace: str, repo_slug: str, pr_id: int) -> Dict:
        """
        Get pull request information
        
        Args:
            workspace: Workspace name
            repo_slug: Repository slug
            pr_id: Pull request ID
            
        Returns:
            Pull request data
        """
        url = f"{self.api_url}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"
        response = requests.get(url, auth=self.auth, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def add_pr_comment(self, workspace: str, repo_slug: str, pr_id: int, comment: str) -> Dict:
        """
        Add a comment to a pull request
        
        Args:
            workspace: Workspace name
            repo_slug: Repository slug
            pr_id: Pull request ID
            comment: Comment text
            
        Returns:
            Created comment data
        """
        url = f"{self.api_url}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
        data = {
            'content': {
                'raw': comment
            }
        }
        
        response = requests.post(url, auth=self.auth, json=data, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.json()
    
    def get_pr_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str:
        """
        Get pull request diff
        
        Args:
            workspace: Workspace name
            repo_slug: Repository slug
            pr_id: Pull request ID
            
        Returns:
            Diff content as string
        """
        url = f"{self.api_url}/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diff"
        response = requests.get(url, auth=self.auth, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        
        return response.text
    
    def review_pull_request(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
        post_comment: bool = True
    ) -> Dict:
        """
        Review a pull request and optionally post the review as a comment
        
        Args:
            workspace: Workspace name
            repo_slug: Repository slug
            pr_id: Pull request ID
            post_comment: Whether to post review as comment
            
        Returns:
            Review results
        """
        # Get PR diff
        diff = self.get_pr_diff(workspace, repo_slug, pr_id)
        
        # Review the changes
        review_result = self.reviewer.review_code_changes(diff)
        
        # Post comment if requested
        if post_comment:
            comment = self.reviewer.format_review_comment(review_result)
            self.add_pr_comment(workspace, repo_slug, pr_id, comment)
        
        return review_result
    
    def create_and_review_pr(
        self,
        repo_path: str,
        repo_url: str,
        source_branch: str,
        destination_branch: str,
        title: str,
        description: str
    ) -> Tuple[Dict, Dict]:
        """
        Create a pull request and immediately review it
        
        Args:
            repo_path: Local repository path
            repo_url: Bitbucket repository URL
            source_branch: Source branch name
            destination_branch: Destination branch name
            title: PR title
            description: PR description
            
        Returns:
            Tuple of (PR data, review results)
        """
        workspace, repo_slug = self.extract_workspace_repo(repo_url)
        
        # Create the PR
        pr_data = self.create_pull_request(
            workspace,
            repo_slug,
            source_branch,
            destination_branch,
            title,
            description
        )
        
        # Review the PR
        review_result = self.review_pull_request(
            workspace,
            repo_slug,
            pr_data['id'],
            post_comment=True
        )
        
        return pr_data, review_result


def apply_bitbucket_code_change(
    goal: str,
    files_to_edit: List[str],
    repo_path: str,
    branch_name: str
) -> Tuple[str, Dict, str]:
    """
    Apply code changes and create a Bitbucket pull request
    
    Args:
        goal: Code enhancement goal
        files_to_edit: List of files to edit
        repo_path: Repository path
        branch_name: Branch name
        
    Returns:
        Tuple of (branch_name, changes dict, PR URL)
    """
    from agent.skills.coder import (
        get_code_context,
        enhance_and_lint_files,
        commit_and_push,
        summarize_change_for_pr,
        get_coding_model_name
    )
    
    # Load configs
    MAIN_REPO = os.getenv("MAIN_REPO")
    CODING_MODEL_NAME = get_coding_model_name()
    target_base = os.getenv("TARGET_BRANCH", "main")
    
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
    
    # PR creation & review
    bitbucket = BitbucketIntegration()
    pr_title, pr_body_llm = summarize_change_for_pr(goal, changes, CODING_MODEL_NAME)
    
    pr_body = (
        pr_body_llm +
        f"\n\nFiles changed: {', '.join(files_to_edit)}\n"
        "This PR was generated by the Curie AI assistant. Please review before merging.\n"
    )
    
    pr_data, review_result = bitbucket.create_and_review_pr(
        repo_path,
        MAIN_REPO,
        branch_name,
        target_base,
        pr_title,
        pr_body
    )
    
    # Add linting results as comment
    lint_comment = "### 🧹 Linting Results\n\n" + "\n".join(
        f"**{fname}:** {result}" for fname, result in lint_results.items()
    )
    workspace, repo_slug = bitbucket.extract_workspace_repo(MAIN_REPO)
    bitbucket.add_pr_comment(workspace, repo_slug, pr_data['id'], lint_comment)
    
    return branch_name, changes, pr_data['links']['html']['href']
