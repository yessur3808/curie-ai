"""Tests for scraper_patterns module to verify UPSERT behavior."""

import sys
import os
from unittest.mock import MagicMock, patch
import pytest

# ---------------------------------------------------------------------------
# Dependency stubs
# ---------------------------------------------------------------------------
# psycopg2 is not installed in the lightweight CI environment; stub it so
# memory.scraper_patterns can be imported without a real DB driver.
for _mod in ("psycopg2", "psycopg2.extras", "psycopg2.extensions", "psycopg2.sql"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Likewise for pymongo (transitively needed by memory/__init__.py).
for _mod in ("pymongo", "pymongo.collection", "pymongo.errors"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# ---------------------------------------------------------------------------
# Memory mock isolation
# ---------------------------------------------------------------------------
# Other test modules that load before this one (e.g. test_connectors.py) may
# have injected a MagicMock for the top-level "memory" package into
# sys.modules.  That prevents Python from traversing to the real
# memory.scraper_patterns submodule.  Remove any such stubs and let the real
# package load (using the db stubs above for the transitive psycopg2 import).
for _k in [k for k in sys.modules if k == "memory" or k.startswith("memory.")]:
    del sys.modules[_k]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory.scraper_patterns import ScraperPatternManager  # noqa: E402


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
