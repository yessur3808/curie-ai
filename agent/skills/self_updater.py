# agent/skills/self_updater.py

"""
Self-Update Module - Allows the AI assistant to update itself safely
"""

import os
import sys
import subprocess
import git
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class SelfUpdater:
    """Handle self-update operations for the AI assistant"""
    
    def __init__(self, repo_path: Optional[str] = None):
        """
        Initialize the self-updater
        
        Args:
            repo_path: Path to the repository (defaults to current working directory)
        """
        self.repo_path = repo_path or os.getcwd()
        self.repo = git.Repo(self.repo_path)
        self.backup_branch = "backup-before-update"
    
    def check_for_updates(self, branch: str = "main") -> Dict[str, any]:
        """
        Check if updates are available
        
        Args:
            branch: Branch to check for updates
            
        Returns:
            Dictionary with update information
        """
        try:
            # Fetch latest changes
            origin = self.repo.remote(name="origin")
            origin.fetch()
            
            # Get current and remote commits
            local_commit = self.repo.head.commit
            remote_commit = self.repo.commit(f"origin/{branch}")
            
            # Check if updates available
            updates_available = local_commit != remote_commit
            
            # Get list of changes
            if updates_available:
                commits_behind = list(self.repo.iter_commits(f'{local_commit}..{remote_commit}'))
                commit_messages = [f"- {c.summary}" for c in commits_behind]
            else:
                commits_behind = []
                commit_messages = []
            
            return {
                'updates_available': updates_available,
                'commits_behind': len(commits_behind),
                'changes': commit_messages,
                'current_commit': str(local_commit)[:7],
                'latest_commit': str(remote_commit)[:7]
            }
            
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return {
                'updates_available': False,
                'error': str(e)
            }
    
    def create_backup(self) -> bool:
        """
        Create a backup branch before updating
        
        Returns:
            True if backup successful, False otherwise
        """
        try:
            # Delete old backup branch if it exists
            if self.backup_branch in self.repo.heads:
                self.repo.delete_head(self.backup_branch, force=True)
            
            # Create new backup branch
            current = self.repo.active_branch
            backup = self.repo.create_head(self.backup_branch, current.commit)
            
            logger.info(f"Created backup branch: {self.backup_branch}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return False
    
    def pull_updates(self, branch: str = "main", force: bool = False) -> Dict[str, any]:
        """
        Pull latest updates from remote
        
        Args:
            branch: Branch to pull from
            force: Whether to force pull (discard local changes)
            
        Returns:
            Dictionary with pull results
        """
        try:
            # Create backup first
            if not self.create_backup():
                return {
                    'success': False,
                    'error': 'Failed to create backup'
                }
            
            # Check for local changes
            if self.repo.is_dirty() and not force:
                return {
                    'success': False,
                    'error': 'Local changes detected. Use force=True to discard them.',
                    'dirty_files': [item.a_path for item in self.repo.index.diff(None)]
                }
            
            # Checkout target branch
            if branch not in self.repo.heads:
                # Create local branch tracking remote
                self.repo.create_head(branch, f"origin/{branch}")
            
            self.repo.heads[branch].checkout()
            
            # Pull updates
            origin = self.repo.remote(name="origin")
            
            if force and self.repo.is_dirty():
                # Discard local changes
                self.repo.git.reset('--hard', f'origin/{branch}')
            
            pull_info = origin.pull()
            
            return {
                'success': True,
                'branch': branch,
                'pull_info': str(pull_info)
            }
            
        except Exception as e:
            logger.error(f"Failed to pull updates: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def update_dependencies(self) -> Dict[str, any]:
        """
        Update Python dependencies from requirements.txt
        
        Returns:
            Dictionary with update results
        """
        requirements_file = os.path.join(self.repo_path, "requirements.txt")
        
        if not os.path.exists(requirements_file):
            return {
                'success': False,
                'error': 'requirements.txt not found'
            }
        
        try:
            # Update pip first
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
                check=True,
                capture_output=True,
                text=True
            )
            
            # Install/update requirements
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", requirements_file],
                check=True,
                capture_output=True,
                text=True
            )
            
            return {
                'success': True,
                'output': result.stdout
            }
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to update dependencies: {e}")
            return {
                'success': False,
                'error': str(e),
                'output': e.stderr
            }
    
    def rollback(self) -> Dict[str, any]:
        """
        Rollback to backup branch
        
        Returns:
            Dictionary with rollback results
        """
        try:
            if self.backup_branch not in self.repo.heads:
                return {
                    'success': False,
                    'error': f'Backup branch {self.backup_branch} not found'
                }
            
            # Checkout backup branch
            self.repo.heads[self.backup_branch].checkout()
            
            logger.info(f"Rolled back to backup branch: {self.backup_branch}")
            return {
                'success': True,
                'message': f'Rolled back to {self.backup_branch}'
            }
            
        except Exception as e:
            logger.error(f"Failed to rollback: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def full_update(
        self,
        branch: str = "main",
        update_deps: bool = True,
        force: bool = False
    ) -> Dict[str, any]:
        """
        Perform a complete update: pull code and update dependencies
        
        Args:
            branch: Branch to pull from
            update_deps: Whether to update dependencies
            force: Whether to force update (discard local changes)
            
        Returns:
            Dictionary with complete update results
        """
        results = {
            'pull': None,
            'dependencies': None,
            'success': False
        }
        
        # Pull code updates
        pull_result = self.pull_updates(branch, force)
        results['pull'] = pull_result
        
        if not pull_result.get('success'):
            return results
        
        # Update dependencies if requested
        if update_deps:
            dep_result = self.update_dependencies()
            results['dependencies'] = dep_result
            
            if not dep_result.get('success'):
                # Dependency update failed, consider rollback
                logger.warning("Dependency update failed. Update completed but dependencies may be outdated.")
        
        results['success'] = True
        return results
    
    def restart_service(self, service_name: Optional[str] = None) -> Dict[str, any]:
        """
        Restart the service (requires proper setup with systemd or similar)
        
        Args:
            service_name: Name of the systemd service (uses SYSTEMD_SERVICE_NAME env if not provided)
            
        Returns:
            Dictionary with restart results
        """
        service_name = service_name or os.getenv('SYSTEMD_SERVICE_NAME')
        restart_timeout = int(os.getenv('SERVICE_RESTART_TIMEOUT', '30'))
        
        if not service_name:
            return {
                'success': False,
                'error': 'Service name not provided. Set SYSTEMD_SERVICE_NAME environment variable.',
                'manual_restart': 'Please restart the service manually to apply updates.'
            }
        
        try:
            # Try to restart using systemctl with configurable timeout
            result = subprocess.run(
                ['sudo', 'systemctl', 'restart', service_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=restart_timeout
            )
            
            return {
                'success': True,
                'message': f'Service {service_name} restarted successfully'
            }
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to restart service: {e}")
            return {
                'success': False,
                'error': str(e),
                'manual_restart': f'Please run: sudo systemctl restart {service_name}'
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'Restart command timed out',
                'manual_restart': f'Please run: sudo systemctl restart {service_name}'
            }


def auto_update(
    branch: str = "main",
    update_deps: bool = True,
    restart: bool = False,
    force: bool = False
) -> Dict[str, any]:
    """
    Convenience function to perform automatic update
    
    Args:
        branch: Branch to update from
        update_deps: Whether to update dependencies
        restart: Whether to restart service after update
        force: Whether to force update (discard local changes)
        
    Returns:
        Complete update results
    """
    updater = SelfUpdater()
    
    # Check for updates first
    check_result = updater.check_for_updates(branch)
    
    if not check_result.get('updates_available'):
        return {
            'success': True,
            'message': 'Already up to date',
            'check': check_result
        }
    
    logger.info(f"Updates available: {check_result['commits_behind']} commits behind")
    
    # Perform full update
    update_result = updater.full_update(branch, update_deps, force)
    
    result = {
        'check': check_result,
        'update': update_result
    }
    
    # Restart if requested and update successful
    if restart and update_result.get('success'):
        restart_result = updater.restart_service()
        result['restart'] = restart_result
    
    result['success'] = update_result.get('success', False)
    
    return result
