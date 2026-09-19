"""Bounded hierarchical retrieval for Curie's long-term memory.

The model never receives the whole store. Candidates are owner-filtered by the
storage layer, gated for status and expiry here, ranked with deterministic
hybrid retrieval, then packed into a small context budget.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Any, Iterable

_STOP_WORDS = frozenset(
    "a about again all am an and any are as at be been before by can could did do "
    "does for from had has have hello hey hi how i if in into is it its me my of on "
    "or our please say something tell that the their them then there these they this "
    "to up us was we were what when where which who why will with would you your".split()
)
_REFERENCE_CUES = re.compile(
    r"\b(?:remember|recall|earlier|before|last time|previously|we discussed|"
    r"we talked about|you know about me)\b",
    re.I,
)
_OPERATIONAL_COMMAND = re.compile(
    r"^(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?))?"
    r"(?:turn|switch|set|start|stop|open|close|lock|unlock|enable|disable|run|"
    r"send|cancel|pause|resume|schedule)\b",
    re.I,
)
_TIER_BY_KIND = {
    "identity": "core",
    "preference": "core",
    "routine": "core",
    "assistant_setting": "core",
    "temporary_context": "episodic",
    "episode": "episodic",
    "project": "archival",
    "relationship": "archival",
    "biography": "archival",
    "hypothesis": "archival",
}
_IMPORTANCE_BY_KIND = {
    "identity": 1.0,
    "assistant_setting": 0.95,
    "preference": 0.8,
    "routine": 0.75,
    "project": 0.75,
    "relationship": 0.7,
    "biography": 0.65,
    "temporary_context": 0.55,
    "episode": 0.5,
    "hypothesis": 0.35,
}
_ALIASES = (
    frozenset({"prefer", "preference", "favorite", "like", "love", "enjoy"}),
    frozenset(
        {
            "food",
            "meal",
            "dish",
            "cuisine",
            "eat",
            "diet",
            "dietary",
            "vegan",
            "vegetarian",
            "allergy",
        }
    ),
    frozenset({"job", "work", "career", "occupation", "profession"}),
    frozenset({"home", "house", "location", "live", "city", "country"}),
    frozenset({"project", "build", "app", "application", "system"}),
    frozenset({"name", "called", "nickname"}),
    frozenset({"remember", "recall", "memory", "discussed", "mentioned"}),
)
_RELATION_TOKENS = frozenset(
    {
        "prefer",
        "preference",
        "favorite",
        "like",
        "love",
        "enjoy",
        "remember",
        "recall",
        "memory",
        "discuss",
        "mention",
    }
)
_TIER_LIMITS = {"core": 3, "episodic": 3, "archival": 4}


@dataclass(slots=True)
class _PreparedMemory:
    """Content-derived retrieval features cached only in process memory."""

    fingerprint: str
    document_tokens: frozenset[str]
    key_tokens: frozenset[str]
    text_normal: str
    key_normal: str
    vector: tuple[float, ...] | None = None


@dataclass(slots=True)
class _PreparedQuery:
    tokens: frozenset[str]
    normal: str
    vector: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class _SemanticMatch:
    coverage: float
    key_coverage: float
    phrase: float
    fuzzy: float
    cheap_score: float
    reason: str


_PREPARED_CACHE: OrderedDict[str, _PreparedMemory] = OrderedDict()
_CACHE_LOCK = threading.RLock()
_METRICS_LOCK = threading.Lock()
_RETRIEVAL_TOTALS: dict[str, float] = {
    "queries": 0,
    "bypassed": 0,
    "candidates_scanned": 0,
    "candidates_reranked": 0,
    "selected": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "total_latency_ms": 0.0,
}


def default_importance(kind: str) -> float:
    """Return a conservative default importance for one typed memory."""
    return _IMPORTANCE_BY_KIND.get(str(kind).casefold(), 0.5)


def memory_tier(memory: dict[str, Any]) -> str:
    """Map a typed memory to its prompt tier."""
    explicit = str(memory.get("tier", "")).casefold()
    if explicit in {"core", "episodic", "archival"}:
        return explicit
    return _TIER_BY_KIND.get(str(memory.get("kind", "")).casefold(), "archival")


def _stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if suffix == "s" and token.endswith("ss"):
            continue
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _tokens(text: str) -> set[str]:
    normalized = str(text).casefold().replace("_", " ")
    return {
        _stem(token)
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) > 1 and token not in _STOP_WORDS
    }


def _expanded(tokens: set[str] | frozenset[str]) -> set[str]:
    result = set(tokens)
    for group in _ALIASES:
        stemmed_group = {_stem(item) for item in group}
        if result & stemmed_group:
            result.update(stemmed_group)
    return result


def _normal(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text).casefold()))


def _memory_text(memory: dict[str, Any]) -> str:
    value = memory.get("value", "")
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, default=str, sort_keys=True)
    return " ".join(
        (
            str(memory.get("key", "")),
            str(value),
            str(memory.get("summary", "")),
            str(memory.get("tags", "")),
        )
    )


def _hashed_vector(
    text: str,
    dimensions: int = 256,
    *,
    tokens: set[str] | frozenset[str] | None = None,
) -> tuple[float, ...]:
    ordered_tokens = sorted(tokens if tokens is not None else _tokens(text))
    features = list(ordered_tokens)
    features.extend(
        f"{left}:{right}" for left, right in zip(ordered_tokens, ordered_tokens[1:])
    )
    compact = _normal(text).replace(" ", "")
    features.extend(
        compact[index : index + 3] for index in range(max(0, len(compact) - 2))
    )
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.blake2b(feature.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest, "big") % dimensions] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(value / norm for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cache_capacity() -> int:
    return max(0, min(_safe_int(os.getenv("MEMORY_RETRIEVAL_CACHE_SIZE"), 4096), 32768))


def _prepare_memory(
    memory: dict[str, Any], capacity: int | None = None
) -> tuple[_PreparedMemory, bool]:
    """Return reusable semantic features and whether they came from the LRU."""
    text = _memory_text(memory)
    fingerprint = hashlib.blake2b(text.encode(), digest_size=16).hexdigest()
    capacity = _cache_capacity() if capacity is None else capacity
    if capacity:
        with _CACHE_LOCK:
            cached = _PREPARED_CACHE.get(fingerprint)
            if cached is not None:
                _PREPARED_CACHE.move_to_end(fingerprint)
                return cached, True

    prepared = _PreparedMemory(
        fingerprint=fingerprint,
        document_tokens=frozenset(_expanded(_tokens(text))),
        key_tokens=frozenset(_expanded(_tokens(str(memory.get("key", ""))))),
        text_normal=_normal(text),
        key_normal=_normal(memory.get("key", "")),
    )
    if capacity:
        with _CACHE_LOCK:
            # Another thread may have prepared the same document while this one
            # was tokenizing it. Reuse that object so its lazy vector is shared.
            cached = _PREPARED_CACHE.get(fingerprint)
            if cached is not None:
                _PREPARED_CACHE.move_to_end(fingerprint)
                return cached, True
            _PREPARED_CACHE[fingerprint] = prepared
            while len(_PREPARED_CACHE) > capacity:
                _PREPARED_CACHE.popitem(last=False)
    return prepared, False


def _memory_vector(
    prepared: _PreparedMemory, memory: dict[str, Any]
) -> tuple[float, ...]:
    if prepared.vector is not None:
        return prepared.vector
    vector = _hashed_vector(_memory_text(memory), tokens=prepared.document_tokens)
    with _CACHE_LOCK:
        prepared.vector = prepared.vector or vector
        return prepared.vector


def _query_vector(query: str, prepared: _PreparedQuery) -> tuple[float, ...]:
    if prepared.vector is None:
        prepared.vector = _hashed_vector(query, tokens=prepared.tokens)
    return prepared.vector


def _record_retrieval_metrics(**increments: float) -> None:
    with _METRICS_LOCK:
        for key, value in increments.items():
            _RETRIEVAL_TOTALS[key] += value


def retrieval_metrics(*, reset: bool = False) -> dict[str, Any]:
    """Return content-free process diagnostics for recall performance."""
    with _METRICS_LOCK:
        snapshot = dict(_RETRIEVAL_TOTALS)
        if reset:
            for key in _RETRIEVAL_TOTALS:
                _RETRIEVAL_TOTALS[key] = 0.0
    with _CACHE_LOCK:
        cache_entries = len(_PREPARED_CACHE)
    attempts = snapshot["cache_hits"] + snapshot["cache_misses"]
    queries = snapshot["queries"]
    return {
        **{
            key: int(value)
            for key, value in snapshot.items()
            if key != "total_latency_ms"
        },
        "total_latency_ms": round(snapshot["total_latency_ms"], 3),
        "average_latency_ms": round(snapshot["total_latency_ms"] / max(1, queries), 3),
        "cache_hit_rate": round(snapshot["cache_hits"] / max(1, attempts), 4),
        "cache_entries": cache_entries,
        "cache_capacity": _cache_capacity(),
    }


def clear_retrieval_cache() -> None:
    """Drop only ephemeral compiled features; persisted memories are untouched."""
    with _CACHE_LOCK:
        _PREPARED_CACHE.clear()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _is_expired(memory: dict[str, Any], now: datetime) -> bool:
    expires_at = _as_datetime(memory.get("expires_at"))
    return bool(expires_at and expires_at <= now)


def _freshness(memory: dict[str, Any], now: datetime) -> float:
    observed = (
        _as_datetime(memory.get("last_confirmed_at"))
        or _as_datetime(memory.get("last_seen_at"))
        or _as_datetime(memory.get("created_at"))
    )
    if not observed:
        return 0.5
    age_days = max(0.0, (now - observed).total_seconds() / 86_400)
    half_life = {"core": 1825.0, "episodic": 90.0, "archival": 365.0}[
        memory_tier(memory)
    ]
    return math.exp(-math.log(2) * age_days / half_life)


def _semantic_match(
    query: _PreparedQuery, memory: _PreparedMemory
) -> _SemanticMatch | None:
    """Cheap lexical gate used before fuzzy/vector reranking."""
    if not query.tokens:
        return None
    overlap = query.tokens & memory.document_tokens
    subject_overlap = overlap - _RELATION_TOKENS
    query_subjects = query.tokens - _RELATION_TOKENS
    coverage = len(overlap) / max(1, len(query.tokens))
    key_coverage = len(query.tokens & memory.key_tokens) / max(1, len(query.tokens))
    phrase = float(
        len(query.normal) >= 3
        and (
            query.normal in memory.text_normal
            or (memory.key_normal and memory.key_normal in query.normal)
        )
    )
    fuzzy = (
        SequenceMatcher(None, query.normal, memory.key_normal).ratio()
        if memory.key_normal
        else 0.0
    )
    semantic_gate = (
        (bool(subject_overlap) if query_subjects else bool(overlap))
        or phrase > 0
        or fuzzy >= 0.62
    )
    if not semantic_gate:
        return None
    cheap_score = 0.46 * coverage + 0.25 * key_coverage + 0.18 * phrase + 0.11 * fuzzy
    if phrase:
        reason = "exact phrase"
    elif coverage >= 0.5:
        reason = "strong topic match"
    elif overlap:
        reason = "keyword match"
    else:
        reason = "fuzzy match"
    return _SemanticMatch(
        coverage=coverage,
        key_coverage=key_coverage,
        phrase=phrase,
        fuzzy=fuzzy,
        cheap_score=cheap_score,
        reason=reason,
    )


def _hybrid_score(
    memory: dict[str, Any],
    prepared_memory: _PreparedMemory,
    match: _SemanticMatch,
    query_vector: tuple[float, ...],
    now: datetime,
) -> float:
    """Apply the more expensive semantic and quality reranking stage."""
    hashed = _cosine(query_vector, _memory_vector(prepared_memory, memory))
    semantic = (
        0.38 * match.coverage
        + 0.2 * match.key_coverage
        + 0.15 * match.phrase
        + 0.12 * match.fuzzy
        + 0.15 * hashed
    )
    confidence = max(0.0, min(_safe_float(memory.get("confidence")), 1.0))
    importance = max(
        0.0,
        min(
            _safe_float(
                memory.get("importance"),
                default_importance(memory.get("kind", "")),
            ),
            1.0,
        ),
    )
    confirmations = max(0, _safe_int(memory.get("confirmation_count")))
    reinforcement = min(math.log1p(confirmations) / math.log(8), 1.0)
    score = min(
        1.0,
        semantic
        + 0.035 * confidence
        + 0.025 * importance
        + 0.02 * reinforcement
        + 0.02 * _freshness(memory, now),
    )
    return score


def rank_memories(
    query: str,
    memories: Iterable[dict[str, Any]],
    *,
    limit: int = 8,
    char_budget: int | None = None,
    explicit_search: bool = False,
) -> list[dict[str, Any]]:
    """Return only relevant, active memories within a strict prompt budget."""
    started = time.perf_counter()
    clean_query = str(query or "").strip()
    if not clean_query:
        _record_retrieval_metrics(
            queries=1, total_latency_ms=(time.perf_counter() - started) * 1000
        )
        return []
    reference_request = bool(_REFERENCE_CUES.search(clean_query))
    if (
        _OPERATIONAL_COMMAND.search(clean_query)
        and not reference_request
        and not explicit_search
    ):
        _record_retrieval_metrics(
            queries=1,
            bypassed=1,
            total_latency_ms=(time.perf_counter() - started) * 1000,
        )
        return []

    now = datetime.now(timezone.utc)
    minimum = _safe_float(os.getenv("MEMORY_RELEVANCE_MIN_SCORE"), 0.28)
    if reference_request:
        minimum = max(0.16, minimum - 0.08)
    if explicit_search:
        minimum = max(0.14, minimum - 0.1)
    if len(_tokens(clean_query)) <= 1:
        minimum += 0.05
    hypothesis_minimum = _safe_float(os.getenv("ADAPTIVE_MEMORY_MIN_CONFIDENCE"), 0.8)
    raw_query_tokens = _tokens(clean_query)
    prepared_query = _PreparedQuery(
        tokens=frozenset(_expanded(raw_query_tokens)),
        normal=_normal(clean_query),
    )
    rerank_limit = max(
        max(1, int(limit)) * (8 if explicit_search else 4),
        min(_safe_int(os.getenv("MEMORY_RERANK_CANDIDATES"), 96), 512),
    )
    cache_capacity = _cache_capacity()

    scanned = 0
    cache_hits = 0
    cache_misses = 0
    candidates: list[
        tuple[float, int, dict[str, Any], _PreparedMemory, _SemanticMatch, str]
    ] = []
    for raw in memories:
        scanned += 1
        memory = raw
        if not memory.get("active", True) or _is_expired(memory, now):
            continue
        status = str(memory.get("status", "verified")).casefold()
        if status not in {"verified", "recorded", "hypothesis"}:
            continue
        if (
            status == "hypothesis"
            and _safe_float(memory.get("confidence")) < hypothesis_minimum
        ):
            continue
        prepared_memory, cache_hit = _prepare_memory(memory, cache_capacity)
        cache_hits += int(cache_hit)
        cache_misses += int(not cache_hit)
        match = _semantic_match(prepared_query, prepared_memory)
        if match is None:
            continue
        candidates.append(
            (
                match.cheap_score,
                _safe_int(memory.get("confirmation_count")),
                memory,
                prepared_memory,
                match,
                status,
            )
        )

    # Only plausible lexical/fuzzy candidates incur vector construction and
    # cosine scoring. This prevents a large archive from dominating latency.
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    candidates = candidates[:rerank_limit]
    query_vector = _query_vector(clean_query, prepared_query) if candidates else ()
    best_by_key: dict[str, dict[str, Any]] = {}
    for _, _, raw, prepared_memory, match, status in candidates:
        memory = dict(raw)
        score = _hybrid_score(
            memory,
            prepared_memory,
            match,
            query_vector,
            now,
        )
        required = minimum + (0.08 if status == "hypothesis" else 0.0)
        if score < required:
            continue
        memory["_relevance"] = round(score, 4)
        memory["_retrieval_reason"] = match.reason
        memory["_memory_tier"] = memory_tier(memory)
        dedupe_key = _normal(memory.get("key", "")) or str(memory.get("id", ""))
        previous = best_by_key.get(dedupe_key)
        if previous is None or memory["_relevance"] > previous["_relevance"]:
            best_by_key[dedupe_key] = memory

    ranked = sorted(
        best_by_key.values(),
        key=lambda item: (
            item["_relevance"],
            _safe_int(item.get("confirmation_count")),
        ),
        reverse=True,
    )
    budget = max(
        300,
        _safe_int(
            (
                char_budget
                if char_budget is not None
                else os.getenv("MEMORY_CONTEXT_CHAR_BUDGET")
            ),
            1600,
        ),
    )
    selected: list[dict[str, Any]] = []
    selected_values: list[str] = []
    tier_counts = {tier: 0 for tier in _TIER_LIMITS}
    used = 0
    for memory in ranked:
        tier = memory["_memory_tier"]
        if tier_counts[tier] >= _TIER_LIMITS[tier]:
            continue
        cost = len(str(memory.get("key", ""))) + len(str(memory.get("value", ""))) + 80
        if selected and used + cost > budget:
            continue
        if cost > budget:
            continue
        value = memory.get("value", "")
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, default=str, sort_keys=True)
        value_normal = _normal(value)
        if len(value_normal) >= 24 and any(
            value_normal == prior
            or (
                (value_normal in prior or prior in value_normal)
                and min(len(value_normal), len(prior))
                / max(len(value_normal), len(prior))
                >= 0.8
            )
            for prior in selected_values
        ):
            continue
        selected.append(memory)
        selected_values.append(value_normal)
        tier_counts[tier] += 1
        used += cost
        if len(selected) >= max(1, int(limit)):
            break
    _record_retrieval_metrics(
        queries=1,
        candidates_scanned=scanned,
        candidates_reranked=len(candidates),
        selected=len(selected),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        total_latency_ms=(time.perf_counter() - started) * 1000,
    )
    return selected


def memory_stats(memories: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return content-free counts for user-facing memory diagnostics."""
    now = datetime.now(timezone.utc)
    stats: dict[str, Any] = {
        "total": 0,
        "active": 0,
        "expired": 0,
        "pending": 0,
        "tiers": {"core": 0, "episodic": 0, "archival": 0},
    }
    for memory in memories:
        stats["total"] += 1
        if _is_expired(memory, now):
            stats["expired"] += 1
        if memory.get("status") == "pending_confirmation":
            stats["pending"] += 1
        if memory.get("active", True) and not _is_expired(memory, now):
            stats["active"] += 1
            stats["tiers"][memory_tier(memory)] += 1
    return stats
