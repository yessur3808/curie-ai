# services/coding_service.py

"""
Standalone Coding Service - Runs independently but can communicate with main assistant
Handles code review, PR/MR management, and self-updates
"""

import os
import sys
import time
import logging
import threading
import queue
from typing import Dict, Optional
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Define logger before any imports that might use it
logger = logging.getLogger(__name__)

# Import with error handling to prevent startup failures
try:
    from agent.skills.code_reviewer import CodeReviewer
except ImportError as e:
    logger.warning(f"CodeReviewer import failed: {e}. Coding service will not be available.")
    CodeReviewer = None

try:
    from agent.skills.gitlab_integration import GitLabIntegration, apply_gitlab_code_change
except ImportError as e:
    logger.warning(f"GitLab integration import failed: {e}")
    GitLabIntegration = None
    apply_gitlab_code_change = None

try:
    from agent.skills.bitbucket_integration import BitbucketIntegration, apply_bitbucket_code_change
except ImportError as e:
    logger.warning(f"Bitbucket integration import failed: {e}")
    BitbucketIntegration = None
    apply_bitbucket_code_change = None

try:
    from agent.skills.self_updater import auto_update
except ImportError as e:
    logger.warning(f"Self-updater import failed: {e}")
    auto_update = None

try:
    from agent.skills.coder import apply_code_change
except ImportError as e:
    logger.warning(f"Coder import failed: {e}")
    apply_code_change = None


class CodingService:
    """
    Standalone service for code-related operations
    Can run in parallel with main assistant and send notifications
    """
    
    def __init__(self, notification_callback=None):
        """
        Initialize the coding service
        
        Args:
            notification_callback: Optional callback function to notify master user
                                 Should accept (message: str, data: dict)
        """
        logger.info("Initializing coding service...")
        
        # Check if required modules are available
        if CodeReviewer is None:
            raise RuntimeError(
                "Cannot initialize coding service: CodeReviewer module failed to import. "
                "Please check that all dependencies are installed (GitPython, etc.)"
            )
        
        self.running = False
        self.task_queue = queue.Queue()
        self.notification_callback = notification_callback
        
        # Initialize code reviewer with error handling
        try:
            self.reviewer = CodeReviewer()
            logger.info("✓ Code reviewer initialized")
        except Exception as e:
            logger.error(f"Failed to initialize code reviewer: {e}", exc_info=True)
            raise RuntimeError(f"Cannot start coding service without code reviewer: {e}")
        
        # Initialize integrations based on available credentials
        self.github_available = bool(os.getenv('GITHUB_TOKEN'))
        self.gitlab_available = bool(os.getenv('GITLAB_TOKEN') and GitLabIntegration is not None)
        self.bitbucket_available = bool(
            os.getenv('BITBUCKET_USERNAME') and 
            os.getenv('BITBUCKET_APP_PASSWORD') and 
            BitbucketIntegration is not None
        )
        
        self.gitlab = None
        self.bitbucket = None
        
        if self.gitlab_available and GitLabIntegration is not None:
            try:
                self.gitlab = GitLabIntegration()
                logger.info("✓ GitLab integration initialized")
            except Exception as e:
                logger.warning(f"✗ GitLab integration failed: {e}")
                self.gitlab_available = False
        
        if self.bitbucket_available and BitbucketIntegration is not None:
            try:
                self.bitbucket = BitbucketIntegration()
                logger.info("✓ Bitbucket integration initialized")
            except Exception as e:
                logger.warning(f"✗ Bitbucket integration failed: {e}")
                self.bitbucket_available = False
        
        logger.info(
            f"Coding service initialized - "
            f"GitHub: {self.github_available}, "
            f"GitLab: {self.gitlab_available}, "
            f"Bitbucket: {self.bitbucket_available}"
        )
        
        # Warn if no platforms are available
        if not (self.github_available or self.gitlab_available or self.bitbucket_available):
            logger.warning("⚠️  No platform integrations available. Set GITHUB_TOKEN, GITLAB_TOKEN, or BITBUCKET credentials to enable platform features.")
    
    def notify_master(self, message: str, data: Optional[Dict] = None):
        """
        Send notification to master user
        
        Args:
            message: Notification message
            data: Optional additional data
        """
        if self.notification_callback:
            try:
                self.notification_callback(message, data or {})
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")
        else:
            logger.info(f"NOTIFICATION: {message}")
    
    def detect_platform(self, repo_url: str) -> str:
        """
        Detect which platform the repository is on
        
        Args:
            repo_url: Repository URL
            
        Returns:
            Platform name: 'github', 'gitlab', 'bitbucket', or 'unknown'
        """
        repo_url_lower = repo_url.lower()
        
        if 'github.com' in repo_url_lower:
            return 'github'
        elif 'gitlab.com' in repo_url_lower or 'gitlab' in repo_url_lower:
            return 'gitlab'
        elif 'bitbucket.org' in repo_url_lower:
            return 'bitbucket'
        else:
            return 'unknown'
    
    def review_code(self, task_data: Dict) -> Dict:
        """
        Review code based on task parameters
        
        Args:
            task_data: Dictionary with review parameters
                - type: 'file', 'diff', or 'pr'
                - platform: 'github', 'gitlab', or 'bitbucket'
                - Additional parameters based on type
                
        Returns:
            Review results
        """
        review_type = task_data.get('type')
        platform = task_data.get('platform', 'github')
        
        try:
            if review_type == 'file':
                # Review a single file
                file_path = task_data['file_path']
                repo_path = task_data.get('repo_path', '.')
                result = self.reviewer.review_file(file_path, repo_path)
                
            elif review_type == 'diff':
                # Review a diff
                diff_content = task_data['diff']
                file_path = task_data.get('file_path')
                result = self.reviewer.review_code_changes(diff_content, file_path)
                
            elif review_type == 'pr' or review_type == 'mr':
                # Review a PR/MR
                if platform == 'gitlab' and self.gitlab:
                    project_path = task_data['project_path']
                    mr_iid = task_data['mr_iid']
                    result = self.gitlab.review_merge_request(project_path, mr_iid, post_comment=True)
                    
                elif platform == 'bitbucket' and self.bitbucket:
                    workspace = task_data['workspace']
                    repo_slug = task_data['repo_slug']
                    pr_id = task_data['pr_id']
                    result = self.bitbucket.review_pull_request(workspace, repo_slug, pr_id, post_comment=True)
                    
                else:
                    result = {
                        'success': False,
                        'error': f'{platform} integration not available or not implemented for PR review'
                    }
            else:
                result = {
                    'success': False,
                    'error': f'Unknown review type: {review_type}'
                }
            
            # Only set success=True if no error exists
            if 'success' not in result:
                result['success'] = True
            return result
            
        except Exception as e:
            logger.error(f"Code review failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def create_pr_mr(self, task_data: Dict) -> Dict:
        """
        Create a PR/MR with code changes
        
        Args:
            task_data: Dictionary with PR/MR parameters
            
        Returns:
            Creation results including URL
        """
        platform = task_data.get('platform')
        goal = task_data.get('goal')
        files_to_edit = task_data.get('files')
        repo_path = task_data.get('repo_path')
        branch_name = task_data.get('branch')
        
        try:
            if platform == 'github' and self.github_available:
                if apply_code_change is None:
                    result = {
                        'success': False,
                        'error': (
                            'GitHub PR creation is not available because the optional '
                            'apply_code_change helper could not be imported.'
                        )
                    }
                else:
                    branch, changes, pr_url = apply_code_change(
                        goal, files_to_edit, repo_path, branch_name
                    )
                    result = {
                        'success': True,
                        'platform': 'github',
                        'branch': branch,
                        'url': pr_url,
                        'files_changed': list(changes.keys())
                    }
                
            elif platform == 'gitlab' and self.gitlab_available:
                branch, changes, mr_url = apply_gitlab_code_change(
                    goal, files_to_edit, repo_path, branch_name
                )
                result = {
                    'success': True,
                    'platform': 'gitlab',
                    'branch': branch,
                    'url': mr_url,
                    'files_changed': list(changes.keys())
                }
                
            elif platform == 'bitbucket' and self.bitbucket_available:
                branch, changes, pr_url = apply_bitbucket_code_change(
                    goal, files_to_edit, repo_path, branch_name
                )
                result = {
                    'success': True,
                    'platform': 'bitbucket',
                    'branch': branch,
                    'url': pr_url,
                    'files_changed': list(changes.keys())
                }
            else:
                result = {
                    'success': False,
                    'error': f'{platform} integration not available or credentials not configured'
                }
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to create PR/MR: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def perform_self_update(self, task_data: Dict) -> Dict:
        """
        Perform self-update operation
        
        Args:
            task_data: Dictionary with update parameters
            
        Returns:
            Update results
        """
        branch = task_data.get('branch', 'main')
        update_deps = task_data.get('update_deps', True)
        force = task_data.get('force', False)
        restart = task_data.get('restart', False)
        
        try:
            result = auto_update(branch, update_deps, restart, force)
            result['success'] = result.get('success', False)
            return result
            
        except Exception as e:
            logger.error(f"Self-update failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def process_task(self, task: Dict):
        """
        Process a single task from the queue
        
        Args:
            task: Task dictionary with 'type' and task-specific data
        """
        task_type = task.get('type')
        task_id = task.get('id', 'unknown')
        
        logger.info(f"Processing task {task_id}: {task_type}")
        self.notify_master(f"🔧 Starting task: {task_type}", {'task_id': task_id})
        
        try:
            if task_type == 'review':
                result = self.review_code(task.get('data', {}))
            elif task_type == 'create_pr':
                result = self.create_pr_mr(task.get('data', {}))
            elif task_type == 'self_update':
                result = self.perform_self_update(task.get('data', {}))
            else:
                result = {
                    'success': False,
                    'error': f'Unknown task type: {task_type}'
                }
            
            # Notify about result
            if result.get('success'):
                self.notify_master(
                    f"✅ Task {task_id} completed successfully",
                    {'task_id': task_id, 'result': result}
                )
            else:
                self.notify_master(
                    f"❌ Task {task_id} failed: {result.get('error', 'Unknown error')}",
                    {'task_id': task_id, 'error': result.get('error')}
                )
            
        except Exception as e:
            logger.error(f"Task {task_id} failed with exception: {e}", exc_info=True)
            self.notify_master(
                f"❌ Task {task_id} crashed: {str(e)}",
                {'task_id': task_id, 'error': str(e)}
            )
    
    def worker_loop(self):
        """Main worker loop that processes tasks from the queue"""
        logger.info("Coding service worker started")
        
        while self.running:
            try:
                # Wait for task with timeout
                task = self.task_queue.get(timeout=1)
                try:
                    self.process_task(task)
                finally:
                    # Always mark task as done, even if processing fails
                    self.task_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
        
        logger.info("Coding service worker stopped")
    
    def start(self):
        """Start the coding service"""
        if self.running:
            logger.warning("Coding service already running")
            return
        
        self.running = True
        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()
        
        logger.info("Coding service started")
        self.notify_master("🚀 Coding service started and ready")
    
    def stop(self):
        """Stop the coding service"""
        if not self.running:
            return
        
        self.running = False
        self.worker_thread.join(timeout=5)
        
        logger.info("Coding service stopped")
        self.notify_master("🛑 Coding service stopped")
    
    def add_task(self, task_type: str, data: Dict) -> str:
        """
        Add a task to the queue
        
        Args:
            task_type: Type of task ('review', 'create_pr', 'self_update')
            data: Task-specific data
            
        Returns:
            Task ID
        """
        task_id = f"{task_type}_{int(time.time() * 1000)}"
        task = {
            'id': task_id,
            'type': task_type,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }
        
        self.task_queue.put(task)
        logger.info(f"Task {task_id} added to queue")
        
        return task_id
    
    def get_status(self) -> Dict:
        """
        Get current service status
        
        Returns:
            Status dictionary
        """
        return {
            'running': self.running,
            'queue_size': self.task_queue.qsize(),
            'integrations': {
                'github': self.github_available,
                'gitlab': self.gitlab_available,
                'bitbucket': self.bitbucket_available
            }
        }


# Standalone service entry point
def run_standalone_service():
    """Run the coding service as a standalone process"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Curie AI Coding Service")
    parser.add_argument('--config', type=str, help="Configuration file path")
    _ = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Create and start service
    service = CodingService()
    service.start()
    
    logger.info("Coding service running. Press Ctrl+C to stop.")
    
    try:
        # Keep running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        service.stop()


if __name__ == '__main__':
    run_standalone_service()
