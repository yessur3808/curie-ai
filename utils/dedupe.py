"""
Thread-safe deduplication cache for handling duplicate updates.

This module provides a thread-safe implementation for tracking processed updates
to prevent duplicate message handling in concurrent environments like Telegram bots.

Inspired by openclaw's dedupe implementation but adapted for Python and this project's needs.
"""

import threading
import time
from typing import Optional


class DedupeCache:
    """
    Thread-safe deduplication cache with TTL and size limits.
    
    This cache is designed to track recently processed updates and prevent
    duplicate processing in multi-threaded environments. It uses a dictionary
    with a lock to ensure thread-safe operations.
    
    Features:
    - Thread-safe access and modification
    - Time-based expiration (TTL)
    - Size-based eviction (FIFO when exceeding max size)
    - Automatic cleanup of expired entries
    """
    
    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000):
        """
        Initialize the dedupe cache.
        
        Args:
            ttl_seconds: Time to live for cache entries in seconds (default: 300)
                        Must be >= 0. Values < 0 will raise ValueError.
            max_size: Maximum number of entries in the cache (default: 1000)
                     Must be >= 0. Values < 0 will raise ValueError.
        
        Raises:
            ValueError: If ttl_seconds or max_size is negative
        """
        if ttl_seconds < 0:
            raise ValueError(f"ttl_seconds must be >= 0, got {ttl_seconds}")
        if max_size < 0:
            raise ValueError(f"max_size must be >= 0, got {max_size}")
        
        self.ttl_seconds = ttl_seconds
        self.max_size = int(max_size)
        self._cache: dict[str, float] = {}
        self._lock = threading.Lock()
    
    def _prune(self, now: float) -> None:
        """
        Remove expired entries and enforce size limit.
        
        This method must be called while holding the lock.
        
        Args:
            now: Current timestamp
        """
        # Remove expired entries
        if self.ttl_seconds > 0:
            cutoff = now - self.ttl_seconds
            expired_keys = [key for key, timestamp in self._cache.items() if timestamp < cutoff]
            for key in expired_keys:
                del self._cache[key]
        
        # Enforce size limit (FIFO eviction)
        if self.max_size > 0:
            while len(self._cache) > self.max_size:
                # Remove oldest entry (first inserted)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
    
    def check(self, key: Optional[str], now: Optional[float] = None) -> bool:
        """
        Check if a key exists in the cache and mark it as seen.
        
        This method is thread-safe and performs the following operations atomically:
        1. Check if the key exists and is not expired
        2. Add the key with current timestamp if new
        3. Prune expired entries and enforce size limits
        
        Note: For strict FIFO behavior, existing keys are NOT updated when checked again.
        This ensures that eviction happens in true insertion order, not access order.
        
        Args:
            key: The key to check (None or empty string returns False)
            now: Current timestamp (uses time.time() if not provided)
        
        Returns:
            True if the key was already in the cache (duplicate detected)
            False if the key is new (first time seeing this key)
        """
        if not key:
            return False
        
        if now is None:
            now = time.time()
        
        with self._lock:
            existing_timestamp = self._cache.get(key)
            
            # Check if key exists and is not expired
            if existing_timestamp is not None:
                if self.ttl_seconds <= 0 or (now - existing_timestamp) < self.ttl_seconds:
                    # Don't update timestamp - maintain strict FIFO order
                    self._prune(now)
                    return True
            
            # New key or expired - add/update it
            self._cache[key] = now
            self._prune(now)
            return False
    
    def clear(self) -> None:
        """Clear all entries from the cache."""
        with self._lock:
            self._cache.clear()
    
    def size(self) -> int:
        """Get the current number of entries in the cache."""
        with self._lock:
            return len(self._cache)
