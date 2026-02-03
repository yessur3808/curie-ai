"""
Tests for the thread-safe deduplication cache.

This test suite verifies that the DedupeCache properly handles
concurrent access and deduplication logic.
"""

import time
import threading
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.dedupe import DedupeCache


def test_dedupe_cache_basic():
    """Test basic deduplication functionality."""
    cache = DedupeCache(ttl_seconds=10, max_size=100)
    
    # First check should return False (new key)
    assert cache.check("key1") is False, "First check should return False"
    
    # Second check should return True (duplicate)
    assert cache.check("key1") is True, "Second check should return True"
    
    # Different key should return False
    assert cache.check("key2") is False, "Different key should return False"
    print("✓ test_dedupe_cache_basic passed")


def test_dedupe_cache_ttl_expiration():
    """Test that entries expire after TTL."""
    cache = DedupeCache(ttl_seconds=1, max_size=100)
    
    # Add a key
    assert cache.check("key1") is False
    assert cache.check("key1") is True
    
    # Wait for expiration
    time.sleep(1.1)
    
    # Key should be expired and treated as new
    assert cache.check("key1") is False, "Expired key should be treated as new"
    print("✓ test_dedupe_cache_ttl_expiration passed")


def test_dedupe_cache_size_limit():
    """Test that cache enforces size limit with FIFO eviction."""
    cache = DedupeCache(ttl_seconds=60, max_size=3)
    
    # Add 3 keys
    assert cache.check("key1") is False
    assert cache.check("key2") is False
    assert cache.check("key3") is False
    assert cache.size() == 3
    
    # Add a 4th key, should evict the first
    assert cache.check("key4") is False
    assert cache.size() == 3
    
    # Now check that key2, key3, key4 are still there (marking them as duplicates)
    assert cache.check("key2") is True
    assert cache.check("key3") is True
    assert cache.check("key4") is True
    
    # Add a new key to verify FIFO eviction continues
    assert cache.check("key5") is False
    # key2 should have been evicted now
    assert cache.size() == 3
    
    print("✓ test_dedupe_cache_size_limit passed")


def test_dedupe_cache_empty_key():
    """Test that empty or None keys return False."""
    cache = DedupeCache(ttl_seconds=10, max_size=100)
    
    assert cache.check(None) is False
    assert cache.check("") is False
    assert cache.size() == 0
    print("✓ test_dedupe_cache_empty_key passed")


def test_dedupe_cache_clear():
    """Test cache clear functionality."""
    cache = DedupeCache(ttl_seconds=10, max_size=100)
    
    cache.check("key1")
    cache.check("key2")
    assert cache.size() == 2
    
    cache.clear()
    assert cache.size() == 0
    
    # Keys should be treated as new after clear
    assert cache.check("key1") is False
    print("✓ test_dedupe_cache_clear passed")


def test_dedupe_cache_thread_safety():
    """Test that cache is thread-safe under concurrent access."""
    cache = DedupeCache(ttl_seconds=60, max_size=1000)
    results = []
    errors = []
    lock = threading.Lock()
    
    def worker(key_prefix: str, iterations: int):
        """Worker function that checks keys in the cache."""
        try:
            local_results = []
            for i in range(iterations):
                key = f"{key_prefix}:{i}"
                result = cache.check(key)
                local_results.append((key, result))
            with lock:
                results.extend(local_results)
        except Exception as e:
            with lock:
                errors.append(e)
    
    # Create multiple threads
    threads = []
    for i in range(10):
        thread = threading.Thread(target=worker, args=(f"thread{i}", 50))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    # Should have no errors
    assert len(errors) == 0, f"Should have no errors, got: {errors}"
    
    # Should have processed all requests
    assert len(results) == 500, f"Should have 500 results, got {len(results)}"
    
    # First occurrence of each key should be False
    seen_keys = set()
    for key, is_duplicate in results:
        if key not in seen_keys:
            assert is_duplicate is False, f"First occurrence of {key} should be False"
            seen_keys.add(key)
        else:
            assert is_duplicate is True, f"Duplicate occurrence of {key} should be True"
    
    print("✓ test_dedupe_cache_thread_safety passed")


def test_dedupe_cache_concurrent_same_key():
    """Test concurrent access to the same key."""
    cache = DedupeCache(ttl_seconds=60, max_size=1000)
    results = []
    errors = []
    lock = threading.Lock()
    
    def worker():
        """Worker function that checks the same key."""
        try:
            result = cache.check("shared_key")
            with lock:
                results.append(result)
        except Exception as e:
            with lock:
                errors.append(e)
    
    # Create multiple threads all checking the same key
    threads = []
    for _ in range(20):
        thread = threading.Thread(target=worker)
        threads.append(thread)
        thread.start()
    
    # Wait for all threads
    for thread in threads:
        thread.join()
    
    # Should have no errors
    assert len(errors) == 0, f"Should have no errors, got: {errors}"
    
    # Should have exactly 20 results
    assert len(results) == 20, f"Should have 20 results, got {len(results)}"
    
    # Exactly one should be False (first one), rest should be True
    false_count = sum(1 for r in results if r is False)
    true_count = sum(1 for r in results if r is True)
    
    assert false_count == 1, f"Exactly one thread should see the key as new, got {false_count}"
    assert true_count == 19, f"19 threads should see the key as duplicate, got {true_count}"
    
    print("✓ test_dedupe_cache_concurrent_same_key passed")


def run_all_tests():
    """Run all tests."""
    tests = [
        test_dedupe_cache_basic,
        test_dedupe_cache_ttl_expiration,
        test_dedupe_cache_size_limit,
        test_dedupe_cache_empty_key,
        test_dedupe_cache_clear,
        test_dedupe_cache_thread_safety,
        test_dedupe_cache_concurrent_same_key,
    ]
    
    print("Running deduplication cache tests...")
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

