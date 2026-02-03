"""Tests for scraper_patterns module to verify UPSERT behavior."""

import pytest
from unittest.mock import MagicMock, patch
from memory.scraper_patterns import ScraperPatternManager


class TestScraperPatternUpsert:
    """Test that save_pattern uses UPSERT to prevent duplicates."""
    
    def test_save_pattern_uses_on_conflict(self):
        """Verify save_pattern SQL includes ON CONFLICT clause."""
        with patch('memory.scraper_patterns.get_pg_conn') as mock_conn:
            # Setup mocks
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
            
            # Call save_pattern
            ScraperPatternManager.save_pattern(
                url='https://example.com',
                domain='example.com',
                query_type='test',
                content_pattern={'selector': '.content'}
            )
            
            # Verify execute was called
            assert mock_cursor.execute.called
            sql_query = mock_cursor.execute.call_args[0][0]
            
            # Verify the SQL contains ON CONFLICT clause
            assert 'ON CONFLICT' in sql_query.upper()
            assert 'DO UPDATE' in sql_query.upper()
            assert '(url, query_type)' in sql_query.lower()
    
    def test_save_pattern_updates_on_duplicate(self):
        """Verify that UPSERT updates existing records on conflict."""
        with patch('memory.scraper_patterns.get_pg_conn') as mock_conn:
            # Setup mocks
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = (1,)
            mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cursor
            
            # Call save_pattern twice with same url and query_type
            ScraperPatternManager.save_pattern(
                url='https://example.com',
                domain='example.com',
                query_type='test',
                content_pattern={'selector': '.old'}
            )
            
            ScraperPatternManager.save_pattern(
                url='https://example.com',
                domain='example.com',
                query_type='test',
                content_pattern={'selector': '.new'}
            )
            
            # Verify execute was called twice
            assert mock_cursor.execute.call_count == 2
            
            # Both calls should have ON CONFLICT
            for call in mock_cursor.execute.call_args_list:
                sql_query = call[0][0]
                assert 'ON CONFLICT' in sql_query.upper()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
