from unittest.mock import patch

import pytest

from utils.ttl_cache import TTLCache, cache_inventory


def test_ttl_cache_expires_values():
    cache = TTLCache(ttl_seconds=5)
    with patch("utils.ttl_cache.time.monotonic", return_value=10):
        cache.set("key", "value")
    with patch("utils.ttl_cache.time.monotonic", return_value=14):
        assert cache.get("key") == "value"
    with patch("utils.ttl_cache.time.monotonic", return_value=15):
        assert cache.get("key") is None


def test_ttl_cache_evicts_least_recently_used_value():
    cache = TTLCache(ttl_seconds=60, max_size=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_personal_cache_requires_owner_and_isolates_values():
    cache = TTLCache(
        60,
        name="test_personal",
        owner_scope="user",
        sensitivity="personal",
        invalidation_event="logout",
    )
    with pytest.raises(ValueError, match="owner_id"):
        cache.set("same", "unsafe")
    cache.set("same", "one", owner_id="u1")
    cache.set("same", "two", owner_id="u2")
    assert cache.get("same", owner_id="u1") == "one"
    assert cache.get("same", owner_id="u2") == "two"


def test_inventory_exposes_policy_and_hit_rate_not_values():
    cache = TTLCache(
        30,
        max_size=1,
        name="inventory_test",
        invalidation_event="source refresh",
    )
    cache.set("secret-key", "secret-value")
    cache.get("secret-key")
    row = next(item for item in cache_inventory() if item["name"] == "inventory_test")
    assert row["ttl_seconds"] == 30
    assert row["max_size"] == 1
    assert row["hit_rate_percent"] == 100.0
    assert "secret-value" not in repr(row)
