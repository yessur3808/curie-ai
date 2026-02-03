"""
Integration tests for Telegram connector's deduplication logic.

Tests the _build_update_key and _is_duplicate_update functions
to ensure proper integration with the DedupeCache.
"""

import sys
import os
from unittest.mock import Mock, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock all external dependencies before importing telegram connector
sys.modules['telegram'] = MagicMock()
sys.modules['telegram.ext'] = MagicMock()
sys.modules['dotenv'] = MagicMock()
sys.modules['agent'] = MagicMock()
sys.modules['agent.core'] = MagicMock()
sys.modules['memory'] = MagicMock()
sys.modules['llm'] = MagicMock()
sys.modules['llm.manager'] = MagicMock()
sys.modules['python_weather'] = MagicMock()

# Mock utils modules that have external dependencies
sys.modules['utils.busy'] = MagicMock()
sys.modules['utils.persona'] = MagicMock()
sys.modules['utils.weather'] = MagicMock()
sys.modules['utils.session'] = MagicMock()
sys.modules['utils.users'] = MagicMock()

# Now import from telegram connector
from connectors.telegram import _build_update_key, _is_duplicate_update, processed_updates


def test_build_update_key_with_user():
    """Test building update key with user information."""
    # Create mock Update object with message and user
    update = Mock()
    update.update_id = 12345
    update.message = Mock()
    update.message.from_user = Mock()
    update.message.from_user.id = 67890
    
    key = _build_update_key(update)
    assert key == "update:12345:user:67890", f"Expected 'update:12345:user:67890', got '{key}'"
    print("✓ test_build_update_key_with_user passed")


def test_build_update_key_without_user():
    """Test building update key without user information."""
    # Create mock Update object without user
    update = Mock()
    update.update_id = 12345
    update.message = None
    
    key = _build_update_key(update)
    assert key == "update:12345", f"Expected 'update:12345', got '{key}'"
    print("✓ test_build_update_key_without_user passed")


def test_build_update_key_with_message_no_user():
    """Test building update key with message but no user."""
    # Create mock Update object with message but no from_user
    update = Mock()
    update.update_id = 12345
    update.message = Mock()
    update.message.from_user = None
    
    key = _build_update_key(update)
    assert key == "update:12345", f"Expected 'update:12345', got '{key}'"
    print("✓ test_build_update_key_with_message_no_user passed")


def test_build_update_key_none_update():
    """Test building update key with None update."""
    key = _build_update_key(None)
    assert key is None, f"Expected None, got '{key}'"
    print("✓ test_build_update_key_none_update passed")


def test_build_update_key_no_update_id():
    """Test building update key without update_id."""
    update = Mock()
    update.update_id = None
    update.message = Mock()
    
    key = _build_update_key(update)
    assert key is None, f"Expected None, got '{key}'"
    print("✓ test_build_update_key_no_update_id passed")


def test_is_duplicate_update_first_time():
    """Test that first occurrence of an update is not marked as duplicate."""
    # Clear the cache first
    processed_updates.clear()
    
    # Create mock Update
    update = Mock()
    update.update_id = 99999
    update.message = Mock()
    update.message.from_user = Mock()
    update.message.from_user.id = 11111
    
    # First time should not be duplicate
    is_dup = _is_duplicate_update(update)
    assert is_dup is False, f"First occurrence should not be duplicate, got {is_dup}"
    print("✓ test_is_duplicate_update_first_time passed")


def test_is_duplicate_update_second_time():
    """Test that second occurrence of an update is marked as duplicate."""
    # Clear the cache first
    processed_updates.clear()
    
    # Create mock Update
    update = Mock()
    update.update_id = 88888
    update.message = Mock()
    update.message.from_user = Mock()
    update.message.from_user.id = 22222
    
    # First check
    first = _is_duplicate_update(update)
    assert first is False, "First occurrence should not be duplicate"
    
    # Second check with same update
    second = _is_duplicate_update(update)
    assert second is True, f"Second occurrence should be duplicate, got {second}"
    print("✓ test_is_duplicate_update_second_time passed")


def test_is_duplicate_update_different_updates():
    """Test that different updates are not marked as duplicates."""
    # Clear the cache first
    processed_updates.clear()
    
    # Create first mock Update
    update1 = Mock()
    update1.update_id = 77777
    update1.message = Mock()
    update1.message.from_user = Mock()
    update1.message.from_user.id = 33333
    
    # Create second mock Update (different)
    update2 = Mock()
    update2.update_id = 77778
    update2.message = Mock()
    update2.message.from_user = Mock()
    update2.message.from_user.id = 33333
    
    # Both should be new
    first = _is_duplicate_update(update1)
    assert first is False, "First update should not be duplicate"
    
    second = _is_duplicate_update(update2)
    assert second is False, f"Second different update should not be duplicate, got {second}"
    print("✓ test_is_duplicate_update_different_updates passed")


def test_is_duplicate_update_none_update():
    """Test that None update is handled gracefully."""
    is_dup = _is_duplicate_update(None)
    assert is_dup is False, f"None update should return False, got {is_dup}"
    print("✓ test_is_duplicate_update_none_update passed")


def test_is_duplicate_update_no_update_id():
    """Test that update without update_id is handled gracefully."""
    update = Mock()
    update.update_id = None
    update.message = Mock()
    
    is_dup = _is_duplicate_update(update)
    assert is_dup is False, f"Update without ID should return False, got {is_dup}"
    print("✓ test_is_duplicate_update_no_update_id passed")


def test_is_duplicate_same_update_id_different_user():
    """Test that same update_id but different user is treated as different update."""
    # Clear the cache first
    processed_updates.clear()
    
    # Create first update
    update1 = Mock()
    update1.update_id = 55555
    update1.message = Mock()
    update1.message.from_user = Mock()
    update1.message.from_user.id = 11111
    
    # Create second update with same ID but different user
    update2 = Mock()
    update2.update_id = 55555
    update2.message = Mock()
    update2.message.from_user = Mock()
    update2.message.from_user.id = 22222
    
    # Both should be treated as new (different composite keys)
    first = _is_duplicate_update(update1)
    assert first is False, "First update should not be duplicate"
    
    second = _is_duplicate_update(update2)
    assert second is False, f"Same update_id but different user should not be duplicate, got {second}"
    print("✓ test_is_duplicate_same_update_id_different_user passed")


def run_all_tests():
    """Run all tests."""
    tests = [
        test_build_update_key_with_user,
        test_build_update_key_without_user,
        test_build_update_key_with_message_no_user,
        test_build_update_key_none_update,
        test_build_update_key_no_update_id,
        test_is_duplicate_update_first_time,
        test_is_duplicate_update_second_time,
        test_is_duplicate_update_different_updates,
        test_is_duplicate_update_none_update,
        test_is_duplicate_update_no_update_id,
        test_is_duplicate_same_update_id_different_user,
    ]
    
    print("Running telegram deduplication integration tests...")
    print("=" * 60)
    
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} error: {e}")
            failed += 1
    
    print("=" * 60)
    if failed == 0:
        print(f"All {len(tests)} tests passed!")
        return 0
    else:
        print(f"{failed}/{len(tests)} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
