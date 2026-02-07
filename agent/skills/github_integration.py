# agent/skills/github_integration.py

"""
Enhanced GitHub Integration - Comprehensive git operations and code editing
Provides full git workflow: add, commit, fetch, pull, cherry-pick, push, and code editing
"""

import os
import logging
from typing import Dict, List, Optional
import git
from github import Github
import subprocess

logger = logging.getLogger(__name__)


class GitHubIntegration:
    """
    Comprehensive GitHub integration with full git operations
    Supports code editing, git workflows, and GitHub API interactions
    """
    
    def __init__(self, token: Optional[str] = None, repo_path: Optional[str] = None):
        """
        Initialize GitHub integration
        
        Args:
            token: GitHub personal access token (uses GITHUB_TOKEN env if not provided).
                If not provided and no GITHUB_TOKEN is set, GitHub API operations will
                be performed unauthenticated and may be rate-limited or fail.
            repo_path: Local repository path (uses current dir if not provided)
        """
        self.token = token or os.getenv('GITHUB_TOKEN')
        if not self.token:
            logger.warning(
                "GitHub token not provided. GitHub API operations will be unauthenticated "
                "and may be rate-limited or fail. Local git and file operations remain available."
            )
        
        self.repo_path = repo_path or os.getcwd()
        
        # Initialize PyGithub client (may be unauthenticated if token is None)
        self.github_client = Github(self.token)
        
        # Initialize GitPython repo
        try:
            self.git_repo = git.Repo(self.repo_path)
            logger.info(f"✓ Git repository initialized at {self.repo_path}")
        except git.exc.InvalidGitRepositoryError:
            logger.warning(f"⚠️  {self.repo_path} is not a git repository")
            self.git_repo = None
    
    def ensure_repo(self):
        """Ensure we have a valid git repository"""
        if self.git_repo is None:
            raise RuntimeError(f"Not a git repository: {self.repo_path}")
    
    # --- File Editing Operations ---
    
    def read_file(self, file_path: str) -> str:
        """
        Read a file from the local repository
        
        Args:
            file_path: Path to file relative to repo root
            
        Returns:
            File contents as string
        """
        full_path = os.path.join(self.repo_path, file_path)
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        with open(full_path, 'r') as f:
            content = f.read()
        
        logger.info(f"✓ Read file: {file_path} ({len(content)} bytes)")
        return content
    
    def write_file(self, file_path: str, content: str) -> bool:
        """
        Write content to a file in the local repository
        
        Args:
            file_path: Path to file relative to repo root
            content: Content to write
            
        Returns:
            True if successful
        """
        full_path = os.path.join(self.repo_path, file_path)
        
        # Create directories if they don't exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, 'w') as f:
            f.write(content)
        
        logger.info(f"✓ Wrote file: {file_path} ({len(content)} bytes)")
        return True
    
    def edit_file(self, file_path: str, old_content: str, new_content: str) -> bool:
        """
        Edit a file by replacing old content with new content
        
        Args:
            file_path: Path to file relative to repo root
            old_content: Content to replace
            new_content: New content
            
        Returns:
            True if successful
        """
        current_content = self.read_file(file_path)
        
        if old_content not in current_content:
            raise ValueError(f"Content to replace not found in {file_path}")
        
        updated_content = current_content.replace(old_content, new_content)
        self.write_file(file_path, updated_content)
        
        logger.info(f"✓ Edited file: {file_path}")
        return True
    
    def create_file(self, file_path: str, content: str) -> bool:
        """
        Create a new file
        
        Args:
            file_path: Path to file relative to repo root
            content: File content
            
        Returns:
            True if successful
        """
        full_path = os.path.join(self.repo_path, file_path)
        
        if os.path.exists(full_path):
            raise FileExistsError(f"File already exists: {file_path}")
        
        return self.write_file(file_path, content)
    
    def delete_file(self, file_path: str) -> bool:
        """
        Delete a file
        
        Args:
            file_path: Path to file relative to repo root
            
        Returns:
            True if successful
        """
        full_path = os.path.join(self.repo_path, file_path)
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        os.remove(full_path)
        logger.info(f"✓ Deleted file: {file_path}")
        return True
    
    # --- Git Operations ---
    
    def git_status(self) -> Dict[str, List[str]]:
        """
        Get git status
        
        Returns:
            Dictionary with staged, unstaged, and untracked files
        """
        self.ensure_repo()
        
        # Get changed files
        changed = [item.a_path for item in self.git_repo.index.diff(None)]
        staged = [item.a_path for item in self.git_repo.index.diff('HEAD')]
        untracked = self.git_repo.untracked_files
        
        status = {
            'staged': staged,
            'unstaged': changed,
            'untracked': list(untracked)
        }
        
        logger.info(f"Git status: {len(staged)} staged, {len(changed)} unstaged, {len(untracked)} untracked")
        return status
    
    def git_add(self, files: Optional[List[str]] = None, all_files: bool = False) -> bool:
        """
        Stage files for commit
        
        Args:
            files: List of files to stage (relative paths)
            all_files: If True, stage all changes
            
        Returns:
            True if successful
        """
        self.ensure_repo()
        
        if all_files:
            self.git_repo.git.add(A=True)
            logger.info("✓ Staged all files")
        elif files:
            self.git_repo.index.add(files)
            logger.info(f"✓ Staged {len(files)} files: {', '.join(files)}")
        else:
            raise ValueError("Must specify files or set all_files=True")
        
        return True
    
    def git_commit(self, message: str, author_name: Optional[str] = None, 
                    author_email: Optional[str] = None) -> str:
        """
        Commit staged changes
        
        Args:
            message: Commit message
            author_name: Optional author name
            author_email: Optional author email
            
        Returns:
            Commit SHA
        """
        self.ensure_repo()
        
        # Set author if provided
        if author_name and author_email:
            actor = git.Actor(author_name, author_email)
            commit = self.git_repo.index.commit(message, author=actor, committer=actor)
        else:
            commit = self.git_repo.index.commit(message)
        
        logger.info(f"✓ Created commit: {commit.hexsha[:7]} - {message}")
        return commit.hexsha
    
    def git_push(self, remote: str = 'origin', branch: Optional[str] = None, 
                 force: bool = False) -> bool:
        """
        Push commits to remote
        
        Args:
            remote: Remote name (default: origin)
            branch: Branch name (uses current if not specified)
            force: Force push
            
        Returns:
            True if successful
        """
        self.ensure_repo()
        
        if branch is None:
            branch = self.git_repo.active_branch.name
        
        origin = self.git_repo.remote(name=remote)
        
        if force:
            origin.push(refspec=f"{branch}:{branch}", force=True)
            logger.info(f"✓ Force pushed to {remote}/{branch}")
        else:
            origin.push(refspec=f"{branch}:{branch}")
            logger.info(f"✓ Pushed to {remote}/{branch}")
        
        return True
    
    def git_pull(self, remote: str = 'origin', branch: Optional[str] = None) -> bool:
        """
        Pull changes from remote
        
        Args:
            remote: Remote name (default: origin)
            branch: Branch name (uses current if not specified)
            
        Returns:
            True if successful
        """
        self.ensure_repo()
        
        if branch is None:
            branch = self.git_repo.active_branch.name
        
        origin = self.git_repo.remote(name=remote)
        origin.pull(branch)
        
        logger.info(f"✓ Pulled from {remote}/{branch}")
        return True
    
    def git_fetch(self, remote: str = 'origin', all_remotes: bool = False) -> bool:
        """
        Fetch changes from remote
        
        Args:
            remote: Remote name (default: origin)
            all_remotes: Fetch from all remotes
            
        Returns:
            True if successful
        """
        self.ensure_repo()
        
        if all_remotes:
            for rem in self.git_repo.remotes:
                rem.fetch()
            logger.info("✓ Fetched from all remotes")
        else:
            origin = self.git_repo.remote(name=remote)
            origin.fetch()
            logger.info(f"✓ Fetched from {remote}")
        
        return True
    
    def git_cherry_pick(self, commit_sha: str) -> bool:
        """
        Cherry-pick a commit
        
        Args:
            commit_sha: Commit SHA to cherry-pick
            
        Returns:
            True if successful
        """
        self.ensure_repo()
        
        self.git_repo.git.cherry_pick(commit_sha)
        logger.info(f"✓ Cherry-picked commit: {commit_sha[:7]}")
        return True
    
    def git_branch(self, branch_name: Optional[str] = None, 
                   create: bool = False, checkout: bool = False) -> List[str]:
        """
        List, create, or checkout branches
        
        Args:
            branch_name: Branch name (for create/checkout)
            create: Create new branch
            checkout: Checkout branch
            
        Returns:
            List of branch names (if listing), or list with new branch name
        """
        self.ensure_repo()
        
        if create and branch_name:
            if branch_name in self.git_repo.heads:
                raise ValueError(f"Branch already exists: {branch_name}")
            new_branch = self.git_repo.create_head(branch_name)
            logger.info(f"✓ Created branch: {branch_name}")
            if checkout:
                new_branch.checkout()
                logger.info(f"✓ Checked out: {branch_name}")
            return [branch_name]
        elif checkout and branch_name:
            if branch_name not in self.git_repo.heads:
                raise ValueError(f"Branch not found: {branch_name}")
            self.git_repo.heads[branch_name].checkout()
            logger.info(f"✓ Checked out: {branch_name}")
            return [branch_name]
        else:
            # List branches
            branches = [head.name for head in self.git_repo.heads]
            logger.info(f"Branches: {', '.join(branches)}")
            return branches
    
    def git_log(self, max_count: int = 10) -> List[Dict[str, str]]:
        """
        Get commit history
        
        Args:
            max_count: Maximum number of commits to return
            
        Returns:
            List of commit dictionaries
        """
        self.ensure_repo()
        
        commits = []
        for commit in self.git_repo.iter_commits(max_count=max_count):
            commits.append({
                'sha': commit.hexsha[:7],
                'message': commit.message.strip(),
                'author': commit.author.name,
                'date': commit.committed_datetime.isoformat(),
            })
        
        logger.info(f"Retrieved {len(commits)} commits")
        return commits
    
    def git_diff(self, cached: bool = False) -> str:
        """
        Get git diff
        
        Args:
            cached: Show diff of staged changes
            
        Returns:
            Diff as string
        """
        self.ensure_repo()
        
        if cached:
            diff = self.git_repo.git.diff('--cached')
        else:
            diff = self.git_repo.git.diff()
        
        logger.info(f"Generated diff ({len(diff)} characters)")
        return diff
    
    # --- GitHub API Operations ---
    
    def create_pull_request(self, title: str, body: str, head: str, base: str = 'main',
                           repo_name: Optional[str] = None) -> Dict:
        """
        Create a pull request on GitHub
        
        Args:
            title: PR title
            body: PR description
            head: Head branch
            base: Base branch (default: main)
            repo_name: Repository name (owner/repo format)
            
        Returns:
            PR data dictionary
        """
        if repo_name is None:
            repo_name = os.getenv('MAIN_REPO')
            if not repo_name:
                raise ValueError("Repository name not provided and MAIN_REPO not set")
            # Extract repo from URL if needed
            if 'github.com' in repo_name:
                from agent.skills.coder import extract_github_repo
                repo_name = extract_github_repo(repo_name)
        
        gh_repo = self.github_client.get_repo(repo_name)
        pr = gh_repo.create_pull(title=title, body=body, head=head, base=base)
        
        logger.info(f"✓ Created PR: {pr.html_url}")
        
        return {
            'number': pr.number,
            'url': pr.html_url,
            'title': pr.title,
            'state': pr.state
        }
    
    def list_pull_requests(self, state: str = 'open', 
                          repo_name: Optional[str] = None) -> List[Dict]:
        """
        List pull requests
        
        Args:
            state: PR state (open, closed, all)
            repo_name: Repository name (owner/repo format)
            
        Returns:
            List of PR dictionaries
        """
        if repo_name is None:
            repo_name = os.getenv('MAIN_REPO')
            if 'github.com' in repo_name:
                from agent.skills.coder import extract_github_repo
                repo_name = extract_github_repo(repo_name)
        
        gh_repo = self.github_client.get_repo(repo_name)
        prs = gh_repo.get_pulls(state=state)
        
        pr_list = []
        for pr in prs:
            pr_list.append({
                'number': pr.number,
                'title': pr.title,
                'state': pr.state,
                'url': pr.html_url,
                'author': pr.user.login
            })
        
        logger.info(f"Retrieved {len(pr_list)} PRs (state: {state})")
        return pr_list
    
    def add_pr_comment(self, pr_number: int, comment: str, 
                       repo_name: Optional[str] = None) -> bool:
        """
        Add comment to a pull request
        
        Args:
            pr_number: PR number
            comment: Comment text
            repo_name: Repository name
            
        Returns:
            True if successful
        """
        if repo_name is None:
            repo_name = os.getenv('MAIN_REPO')
            if not repo_name:
                logger.error(
                    "MAIN_REPO environment variable is not set and no repo_name was provided "
                    "to add_pr_comment. Cannot determine target repository."
                )
                raise ValueError(
                    "Repository name not provided. Set the MAIN_REPO environment variable "
                    "or pass repo_name explicitly to add_pr_comment."
                )
            if 'github.com' in repo_name:
                from agent.skills.coder import extract_github_repo
                repo_name = extract_github_repo(repo_name)
        
        gh_repo = self.github_client.get_repo(repo_name)
        pr = gh_repo.get_pull(pr_number)
        pr.create_issue_comment(comment)
        
        logger.info(f"✓ Added comment to PR #{pr_number}")
        return True


# Convenience functions for easy use

def get_github_integration(repo_path: Optional[str] = None) -> GitHubIntegration:
    """Get a GitHub integration instance"""
    return GitHubIntegration(repo_path=repo_path)


def quick_commit_and_push(files: List[str], message: str, 
                          branch: Optional[str] = None, repo_path: str = '.') -> bool:
    """
    Quick function to stage, commit, and push files
    
    Args:
        files: List of files to commit
        message: Commit message
        branch: Branch to push to (uses current if not specified)
        repo_path: Repository path
        
    Returns:
        True if successful
    """
    gh = GitHubIntegration(repo_path=repo_path)
    gh.git_add(files=files)
    gh.git_commit(message)
    gh.git_push(branch=branch)
    logger.info(f"✓ Quick commit and push completed")
    return True


def edit_and_commit(file_path: str, old_content: str, new_content: str, 
                    commit_message: str, repo_path: str = '.') -> bool:
    """
    Quick function to edit a file and commit the change
    
    Args:
        file_path: File to edit
        old_content: Content to replace
        new_content: New content
        commit_message: Commit message
        repo_path: Repository path
        
    Returns:
        True if successful
    """
    gh = GitHubIntegration(repo_path=repo_path)
    gh.edit_file(file_path, old_content, new_content)
    gh.git_add(files=[file_path])
    gh.git_commit(commit_message)
    logger.info(f"✓ Edited and committed {file_path}")
    return True
