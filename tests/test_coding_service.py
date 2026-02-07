# tests/test_coding_service.py

"""
Tests for the Coding Service module
"""

import pytest
import time
from unittest.mock import Mock, patch
from services.coding_service import CodingService


class TestCodingService:
    """Test cases for CodingService class"""
    
    @pytest.fixture
    def service(self):
        """Create a CodingService instance for testing"""
        # Mock the notification callback
        callback = Mock()
        with patch.dict('os.environ', {
            'GITHUB_TOKEN': 'fake_token',
            'GITLAB_TOKEN': '',
            'BITBUCKET_USERNAME': '',
            'BITBUCKET_APP_PASSWORD': ''
        }):
            service = CodingService(notification_callback=callback)
        return service
    
    def test_service_initialization(self, service):
        """Test that service initializes correctly"""
        assert service.running == False
        assert service.notification_callback is not None
        assert service.reviewer is not None
        assert hasattr(service, 'task_queue')
    
    def test_detect_platform_github(self, service):
        """Test platform detection for GitHub URLs"""
        assert service.detect_platform('https://github.com/user/repo') == 'github'
        assert service.detect_platform('git@github.com:user/repo.git') == 'github'
    
    def test_detect_platform_gitlab(self, service):
        """Test platform detection for GitLab URLs"""
        assert service.detect_platform('https://gitlab.com/user/repo') == 'gitlab'
        assert service.detect_platform('https://gitlab.example.com/user/repo') == 'gitlab'
    
    def test_detect_platform_bitbucket(self, service):
        """Test platform detection for Bitbucket URLs"""
        assert service.detect_platform('https://bitbucket.org/user/repo') == 'bitbucket'
    
    def test_detect_platform_unknown(self, service):
        """Test platform detection for unknown URLs"""
        assert service.detect_platform('https://example.com/user/repo') == 'unknown'
    
    def test_add_task(self, service):
        """Test adding tasks to queue"""
        task_id = service.add_task('review', {'type': 'file', 'file_path': 'test.py'})
        
        assert isinstance(task_id, str)
        assert 'review' in task_id
        assert service.task_queue.qsize() == 1
    
    def test_get_status(self, service):
        """Test getting service status"""
        status = service.get_status()
        
        assert isinstance(status, dict)
        assert 'running' in status
        assert 'queue_size' in status
        assert 'integrations' in status
        assert status['running'] == False
        assert status['queue_size'] == 0
    
    def test_start_stop(self, service):
        """Test starting and stopping the service"""
        service.start()
        assert service.running == True
        assert service.worker_thread.is_alive()
        
        service.stop()
        assert service.running == False
    
    def test_notify_master(self, service):
        """Test notification callback"""
        service.notify_master("Test message", {"key": "value"})
        
        # Check that callback was called
        service.notification_callback.assert_called_once()
        args = service.notification_callback.call_args
        assert args[0][0] == "Test message"
        assert args[0][1] == {"key": "value"}
    
    def test_review_code_file_type(self, service):
        """Test code review with file type"""
        task_data = {
            'type': 'file',
            'file_path': 'nonexistent.py',
            'repo_path': '.'
        }
        
        result = service.review_code(task_data)
        
        assert isinstance(result, dict)
        # Should fail gracefully for nonexistent file
        assert 'error' in result or 'issues' in result
    
    def test_review_code_unknown_type(self, service):
        """Test code review with unknown type"""
        task_data = {
            'type': 'invalid_type'
        }
        
        result = service.review_code(task_data)
        
        assert result['success'] == False
        assert 'error' in result
        assert 'Unknown review type' in result['error']
    
    def test_perform_self_update_mock(self, service):
        """Test self-update with mocked auto_update"""
        with patch('services.coding_service.auto_update') as mock_update:
            mock_update.return_value = {'success': True, 'message': 'Updated'}
            
            task_data = {
                'branch': 'main',
                'update_deps': True,
                'force': False,
                'restart': False
            }
            
            result = service.perform_self_update(task_data)
            
            assert result['success'] == True
            mock_update.assert_called_once_with('main', True, False, False)
    
    def test_process_task_unknown_type(self, service):
        """Test processing unknown task type"""
        task = {
            'id': 'test_123',
            'type': 'unknown_type',
            'data': {}
        }
        
        # Should not raise exception
        service.process_task(task)
        
        # Check notification was called
        assert service.notification_callback.called


class TestCodingServiceIntegration:
    """Integration tests for CodingService"""
    
    @pytest.fixture
    def service(self):
        """Create a CodingService instance for integration testing"""
        callback = Mock()
        with patch.dict('os.environ', {
            'GITHUB_TOKEN': 'fake_token'
        }):
            service = CodingService(notification_callback=callback)
        return service
    
    def test_task_queue_processing(self, service):
        """Test that tasks are processed from queue"""
        service.start()
        
        # Add a simple task
        task_id = service.add_task('review', {
            'type': 'file',
            'file_path': 'test.py'
        })
        
        # Wait briefly for processing
        time.sleep(0.5)
        
        # Queue should be empty after processing
        assert service.task_queue.qsize() == 0
        
        service.stop()
    
    def test_parallel_tasks(self, service):
        """Test adding multiple tasks"""
        service.start()
        
        # Add multiple tasks
        task_ids = []
        for i in range(3):
            task_id = service.add_task('review', {
                'type': 'diff',
                'diff': f'test diff {i}',
                'file_path': f'file{i}.py'
            })
            task_ids.append(task_id)
        
        assert len(task_ids) == 3
        assert len(set(task_ids)) == 3  # All unique
        
        # Wait for processing
        time.sleep(1)
        
        service.stop()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
