"""
Tests for the message-level deduplication used by the Telegram (and other) connectors.

The Telegram connector relies on ChatWorkflow.MessageDedupeCache to avoid
processing the same Telegram update_id twice.  These tests verify the cache
behaviour through the public interface exposed by ChatWorkflow.
"""

import sys
import os
import threading
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.chat_workflow import MessageDedupeCache  # noqa: E402

# ---------------------------------------------------------------------------
# MessageDedupeCache unit tests
# ---------------------------------------------------------------------------


def test_cache_miss_on_first_message():
    """A brand-new message key must not be found in an empty cache."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    result = cache.get("telegram", "chat_1", "msg_1")
    assert result is None, "First lookup should return None (cache miss)"


def test_cache_hit_after_set():
    """After storing a response the same key must return it."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    cache.set("telegram", "chat_1", "msg_1", "Hello!")
    result = cache.get("telegram", "chat_1", "msg_1")
    assert result == "Hello!"


def test_native_write_failure_keeps_the_completed_response_in_process_cache():
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)

    with patch(
        "services.runtime_kernel.store_ingress_response",
        side_effect=RuntimeError("disk I/O error"),
    ):
        cache.set("telegram", "chat_1", "msg_1", "Hello!")

    with patch("services.runtime_kernel.ingress_response", return_value=None):
        assert cache.get("telegram", "chat_1", "msg_1") == "Hello!"


def test_native_read_failure_falls_back_to_process_cache():
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    key = "telegram:chat_1:msg_1"
    cache.cache[key] = (1_000_000.0, "Cached reply")

    with (
        patch(
            "services.runtime_kernel.ingress_response",
            side_effect=RuntimeError("disk I/O error"),
        ),
        patch("agent.chat_workflow.time.time", return_value=1_000_001.0),
    ):
        assert cache.get("telegram", "chat_1", "msg_1") == "Cached reply"


def test_different_message_ids_are_independent():
    """Two messages with different IDs on the same chat must not collide."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    cache.set("telegram", "chat_1", "msg_1", "Response A")
    assert cache.get("telegram", "chat_1", "msg_2") is None


def test_different_platforms_are_independent():
    """Same chat/message IDs on different platforms must be independent entries."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    cache.set("telegram", "chat_1", "msg_1", "Telegram response")
    assert cache.get("discord", "chat_1", "msg_1") is None


def test_different_chats_are_independent():
    """Same message ID in different chats must be independent entries."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    cache.set("telegram", "chat_1", "msg_42", "Chat 1 response")
    assert cache.get("telegram", "chat_2", "msg_42") is None


def test_ttl_expiry():
    """Entries whose TTL has elapsed must no longer be returned.

    We control the clock by patching ``time.time`` in the chat_workflow module
    instead of sleeping, so the test is deterministic regardless of host load.
    """
    cache = MessageDedupeCache(ttl_seconds=100, max_size=100)
    fake_start = 1_000_000.0

    with patch("agent.chat_workflow.time.time", return_value=fake_start):
        cache.set("telegram", "chat_1", "msg_1", "Cached")

    # Retrieve before TTL elapses — should hit.
    with patch("agent.chat_workflow.time.time", return_value=fake_start + 50):
        assert cache.get("telegram", "chat_1", "msg_1") == "Cached"

    # Retrieve after TTL elapses — should miss.
    with patch("agent.chat_workflow.time.time", return_value=fake_start + 101):
        assert (
            cache.get("telegram", "chat_1", "msg_1") is None
        ), "Entry should have expired after TTL"


def test_fifo_eviction_at_max_size():
    """When the cache is full, the oldest entry must be evicted first."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=3)
    cache.set("telegram", "chat", "msg_1", "r1")
    cache.set("telegram", "chat", "msg_2", "r2")
    cache.set("telegram", "chat", "msg_3", "r3")

    # Adding a 4th entry must evict msg_1 (oldest)
    cache.set("telegram", "chat", "msg_4", "r4")

    assert (
        cache.get("telegram", "chat", "msg_1") is None
    ), "Oldest entry should be evicted"
    assert cache.get("telegram", "chat", "msg_2") == "r2"
    assert cache.get("telegram", "chat", "msg_3") == "r3"
    assert cache.get("telegram", "chat", "msg_4") == "r4"


def test_overwrite_existing_key():
    """Setting a key that already exists must update the stored response."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=100)
    cache.set("telegram", "chat_1", "msg_1", "Original")
    cache.set("telegram", "chat_1", "msg_1", "Updated")
    assert cache.get("telegram", "chat_1", "msg_1") == "Updated"


def test_thread_safety():
    """Concurrent writes from multiple threads must not corrupt the cache."""
    cache = MessageDedupeCache(ttl_seconds=60, max_size=1000)
    errors = []

    def writer(start: int):
        try:
            for i in range(start, start + 50):
                cache.set("telegram", "chat", f"msg_{i}", f"resp_{i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i * 50,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread-safety errors: {errors}"
