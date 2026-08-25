"""Policy-described, bounded TTL caches for safe read-only data."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import threading
import time


@dataclass(frozen=True, slots=True)
class CachePolicy:
    name: str
    owner_scope: str
    ttl_seconds: float
    max_size: int
    invalidation_event: str
    sensitivity: str = "public"

    def __post_init__(self):
        if self.owner_scope not in {"public", "user", "process", "device"}:
            raise ValueError("Cache owner_scope is invalid")
        if self.sensitivity not in {"public", "internal", "personal", "sensitive"}:
            raise ValueError("Cache sensitivity is invalid")
        if self.sensitivity in {"personal", "sensitive"} and self.owner_scope != "user":
            raise ValueError("Personal or sensitive caches must be user-scoped")
        if self.ttl_seconds <= 0 or self.max_size <= 0:
            raise ValueError("Cache TTL and maximum size must be positive")


_REGISTRY: dict[str, "TTLCache"] = {}
_REGISTRY_LOCK = threading.Lock()


class TTLCache:
    def __init__(
        self,
        ttl_seconds: float,
        max_size: int = 128,
        *,
        name: str | None = None,
        owner_scope: str = "public",
        invalidation_event: str = "TTL expiry or explicit clear",
        sensitivity: str = "public",
    ):
        self.policy = CachePolicy(
            name=name or f"anonymous-{id(self):x}",
            owner_scope=owner_scope,
            ttl_seconds=float(ttl_seconds),
            max_size=int(max_size),
            invalidation_event=invalidation_event,
            sensitivity=sensitivity,
        )
        self.ttl_seconds = self.policy.ttl_seconds
        self.max_size = self.policy.max_size
        self._values = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0
        with _REGISTRY_LOCK:
            _REGISTRY[self.policy.name] = self

    def _key(self, key, owner_id: str | None):
        if self.policy.owner_scope == "user":
            if not owner_id:
                raise ValueError(f"Cache {self.policy.name!r} requires owner_id")
            return str(owner_id), key
        return key

    def get(self, key, default=None, *, owner_id: str | None = None):
        key = self._key(key, owner_id)
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if item is None:
                self._misses += 1
                return default
            expires_at, value = item
            if expires_at <= now:
                del self._values[key]
                self._misses += 1
                self._expirations += 1
                return default
            self._values.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key, value, *, owner_id: str | None = None) -> None:
        key = self._key(key, owner_id)
        with self._lock:
            self._values[key] = (time.monotonic() + self.ttl_seconds, value)
            self._values.move_to_end(key)
            while len(self._values) > self.max_size:
                self._values.popitem(last=False)
                self._evictions += 1

    def clear(self, *, owner_id: str | None = None) -> None:
        with self._lock:
            if self.policy.owner_scope != "user" or owner_id is None:
                self._values.clear()
            else:
                owner = str(owner_id)
                for key in [key for key in self._values if key[0] == owner]:
                    del self._values[key]

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                **asdict(self.policy),
                "size": len(self._values),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_percent": (
                    round(self._hits / total * 100, 1) if total else 0.0
                ),
                "evictions": self._evictions,
                "expirations": self._expirations,
            }


def cache_inventory() -> list[dict]:
    """Return policy and hit-rate metadata without exposing cached values."""
    with _REGISTRY_LOCK:
        caches = list(_REGISTRY.values())
    return sorted((cache.stats() for cache in caches), key=lambda item: item["name"])
