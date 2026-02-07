# tests/test_self_updater.py

"""
Tests for the Self-Updater module
"""

import pytest
import os
import tempfile
import shutil
from unittest.mock import Mock, patch, MagicMock
import git
from agent.skills.self_updater import SelfUpdater, auto_update


class TestSelfUpdater:
    """Test cases for SelfUpdater class"""
    
    @pytest.fixture
    def temp_repo(self):
        """Create a temporary git repository for testing"""
        temp_dir = tempfile.mkdtemp()
        
        # Initialize git repo
        repo = git.Repo.init(temp_dir)
        
        # Create initial commit
        test_file = os.path.join(temp_dir, 'test.txt')
        with open(test_file, 'w') as f:
            f.write('initial content')
        
        repo.index.add(['test.txt'])
        repo.index.commit('Initial commit')
        
        yield temp_dir, repo
        
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_initialization(self, temp_repo):
        """Test SelfUpdater initialization"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        assert updater.repo_path == temp_dir
        assert updater.repo is not None
        assert updater.backup_branch == "backup-before-update"
    
    def test_create_backup(self, temp_repo):
        """Test backup branch creation"""
        temp_dir, repo = temp_repo
        updater = SelfUpdater(temp_dir)
        
        result = updater.create_backup()
        
        assert result == True
        assert updater.backup_branch in [head.name for head in repo.heads]
    
    def test_create_backup_overwrites_existing(self, temp_repo):
        """Test that creating backup overwrites existing backup branch"""
        temp_dir, repo = temp_repo
        updater = SelfUpdater(temp_dir)
        
        # Create first backup
        updater.create_backup()
        first_commit = repo.heads[updater.backup_branch].commit
        
        # Make a new commit
        test_file = os.path.join(temp_dir, 'test.txt')
        with open(test_file, 'a') as f:
            f.write('\nnew line')
        repo.index.add(['test.txt'])
        repo.index.commit('Second commit')
        
        # Create second backup
        updater.create_backup()
        second_commit = repo.heads[updater.backup_branch].commit
        
        # Should be different commits
        assert first_commit != second_commit
    
    def test_rollback(self, temp_repo):
        """Test rollback to backup branch"""
        temp_dir, repo = temp_repo
        updater = SelfUpdater(temp_dir)
        
        # Create backup
        updater.create_backup()
        backup_commit = repo.heads[updater.backup_branch].commit
        
        # Make a change
        test_file = os.path.join(temp_dir, 'test.txt')
        with open(test_file, 'w') as f:
            f.write('modified content')
        repo.index.add(['test.txt'])
        repo.index.commit('Modification')
        
        # Rollback
        result = updater.rollback()
        
        assert result['success'] == True
        assert repo.head.commit == backup_commit
    
    def test_rollback_without_backup(self, temp_repo):
        """Test rollback without existing backup"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        result = updater.rollback()
        
        assert result['success'] == False
        assert 'not found' in result['error']
    
    @patch('subprocess.run')
    def test_update_dependencies(self, mock_run, temp_repo):
        """Test dependency update"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        # Create requirements.txt
        req_file = os.path.join(temp_dir, 'requirements.txt')
        with open(req_file, 'w') as f:
            f.write('pytest==7.0.0\n')
        
        # Mock successful subprocess calls
        mock_run.return_value = MagicMock(stdout='Success', stderr='')
        
        result = updater.update_dependencies()
        
        assert result['success'] == True
        assert mock_run.call_count == 2  # pip upgrade + install requirements
    
    def test_update_dependencies_no_file(self, temp_repo):
        """Test dependency update without requirements.txt"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        result = updater.update_dependencies()
        
        assert result['success'] == False
        assert 'not found' in result['error']
    
    @patch.object(SelfUpdater, 'pull_updates')
    @patch.object(SelfUpdater, 'update_dependencies')
    def test_full_update_success(self, mock_deps, mock_pull, temp_repo):
        """Test full update with mocked operations"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        # Mock successful operations
        mock_pull.return_value = {'success': True, 'branch': 'main'}
        mock_deps.return_value = {'success': True, 'output': 'Dependencies updated'}
        
        result = updater.full_update('main', update_deps=True, force=False)
        
        assert result['success'] == True
        assert result['pull']['success'] == True
        assert result['dependencies']['success'] == True
    
    @patch.object(SelfUpdater, 'pull_updates')
    def test_full_update_pull_fails(self, mock_pull, temp_repo):
        """Test full update when pull fails"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        # Mock failed pull
        mock_pull.return_value = {'success': False, 'error': 'Pull failed'}
        
        result = updater.full_update('main', update_deps=True, force=False)
        
        assert result['success'] == False
        assert result['pull']['success'] == False
    
    @patch('subprocess.run')
    def test_restart_service_success(self, mock_run, temp_repo):
        """Test service restart with mocked systemctl"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        # Mock successful restart
        mock_run.return_value = MagicMock(stdout='Success', stderr='')
        
        result = updater.restart_service('test-service')
        
        assert result['success'] == True
        mock_run.assert_called_once()
    
    def test_restart_service_no_name(self, temp_repo):
        """Test service restart without service name"""
        temp_dir, _ = temp_repo
        updater = SelfUpdater(temp_dir)
        
        result = updater.restart_service()
        
        assert result['success'] == False
        assert 'not provided' in result['error']
        assert 'manual_restart' in result


class TestAutoUpdate:
    """Test cases for auto_update function"""
    
    @patch('agent.skills.self_updater.SelfUpdater')
    def test_auto_update_no_updates(self, mock_updater_class):
        """Test auto_update when no updates available"""
        mock_updater = Mock()
        mock_updater.check_for_updates.return_value = {
            'updates_available': False,
            'commits_behind': 0
        }
        mock_updater_class.return_value = mock_updater
        
        result = auto_update('main', True, False, False)
        
        assert result['success'] == True
        assert 'Already up to date' in result['message']
        mock_updater.full_update.assert_not_called()
    
    @patch('agent.skills.self_updater.SelfUpdater')
    def test_auto_update_with_updates(self, mock_updater_class):
        """Test auto_update when updates are available"""
        mock_updater = Mock()
        mock_updater.check_for_updates.return_value = {
            'updates_available': True,
            'commits_behind': 3
        }
        mock_updater.full_update.return_value = {
            'success': True,
            'pull': {'success': True},
            'dependencies': {'success': True}
        }
        mock_updater_class.return_value = mock_updater
        
        result = auto_update('main', True, False, False)
        
        assert result['success'] == True
        mock_updater.full_update.assert_called_once()
    
    @patch('agent.skills.self_updater.SelfUpdater')
    def test_auto_update_with_restart(self, mock_updater_class):
        """Test auto_update with restart option"""
        mock_updater = Mock()
        mock_updater.check_for_updates.return_value = {
            'updates_available': True,
            'commits_behind': 1
        }
        mock_updater.full_update.return_value = {'success': True}
        mock_updater.restart_service.return_value = {'success': True}
        mock_updater_class.return_value = mock_updater
        
        result = auto_update('main', True, True, False)
        
        assert result['success'] == True
        mock_updater.restart_service.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
