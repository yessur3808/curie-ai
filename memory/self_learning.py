"""Controlled adaptation with provenance, evaluation gates, and rollback.

This module is deliberately configuration-first.  Curie may observe outcomes,
draft candidates, and evaluate them, but inferred behavior never becomes live
without the promotion gates defined here.  Generated code is never imported or
executed by this module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import IntEnum
import hashlib
import json
import os
import re
from typing import Any, Callable, Mapping
import uuid


class LearningLevel(IntEnum):
    SESSION = 0
    EXPLICIT_PREFERENCE = 1
    INFERRED_PATTERN = 2
    OPTIMIZATION = 3
    CODE_OR_SKILL = 4


EVENT_SOURCES = frozenset(
    {
        "explicit_correction",
        "positive_feedback",
        "negative_feedback",
        "retry_requested",
        "restated_command",
        "topic_rejected",
        "tool_verification_mismatch",
        "request_edited",
        "request_cancelled",
        "human_evaluation",
        "release_scenario_failure",
        "connector_delivery_failure",
        "memory_correction",
        "model_abstention",
        "operational_signal",
        "explicit_preference",
        "session_adaptation",
    }
)

_CANDIDATE_STATUSES = frozenset(
    {
        "insufficient_evidence",
        "pending_evaluation",
        "evaluation_failed",
        "shadow",
        "awaiting_human_approval",
        "staged",
        "canary",
        "promoted",
        "rejected",
        "rolled_back",
        "expired",
    }
)
_EVALUATION_STAGES = ("unit", "targeted", "held_out", "adversarial", "full")
_SESSION_KEY = "controlled_session_adaptation_v1"
_SESSION_TTL_MINUTES = int(os.getenv("LEARNING_SESSION_TTL_MINUTES", "180"))
_LEVEL2_MIN_EVIDENCE = int(os.getenv("LEARNING_LEVEL2_MIN_EVIDENCE", "3"))
_SHADOW_MIN_OBSERVATIONS = int(os.getenv("LEARNING_SHADOW_MIN_OBSERVATIONS", "20"))
_CANARY_MIN_OBSERVATIONS = int(os.getenv("LEARNING_CANARY_MIN_OBSERVATIONS", "20"))
_SENSITIVE = re.compile(
    r"password|passcode|secret|token|credential|private.?key|api.?key|cookie|"
    r"credit.?card|bank.?account|passport|medical|diagnosis|biometric",
    re.I,
)
_AUTHORITY = re.compile(
    r"permission|role|authority|is_master|admin|allowlist|approval_policy|"
    r"credential|secret|token|protected_branch|release_gate",
    re.I,
)
_LEARNING_COMMAND = re.compile(
    r"^/?learning(?:\s+(inspect|events|reject|rollback))?(?:\s+(.+?))?$", re.I
)
_SESSION_LENGTH = re.compile(
    r"\b(?:for now|for this (?:chat|conversation|session)|just this time)\b"
    r".{0,60}\b(?:be|keep (?:it|your replies?))\s+"
    r"(concise|brief|short|balanced|detailed|verbose)\b",
    re.I,
)
_SESSION_ALIAS = re.compile(
    r"\b(?:for now|in this (?:chat|conversation|session))[, ]+"
    r"(?:when i say|by)\s+[\"']?([^\"']{2,60}?)[\"']?\s+"
    r"(?:i mean|mean|refer to)\s+[\"']?([^\"']{2,80})[\"']?[.!]*$",
    re.I,
)
_SESSION_TOPIC = re.compile(
    r"^\s*(?:for now|for this (?:chat|conversation|session))[, ]+"
    r"(?:let(?:'s| us)|we(?:'re| are)|focus on|stick to)\s+(.{2,200})[.!]*$",
    re.I,
)
_SESSION_CONSTRAINT = re.compile(
    r"^\s*(?:for now|for this (?:task|chat|conversation|session))[, ]+"
    r"((?:only|do not|don't|avoid|keep|use)\b.{2,200})[.!]*$",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: _json_safe(item)
            for key, item in value.items()
            if not _SENSITIVE.search(str(key))
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in list(value)[:50]]
    if isinstance(value, str):
        if _SENSITIVE.search(value):
            return "[redacted]"
        return value[:300]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:300]


def _content_features(text: str) -> dict[str, Any]:
    clean = " ".join(str(text or "").split())
    return {
        "content_hash": _hash(clean),
        "character_count": len(clean),
        "word_count": len(clean.split()),
        "contains_negation": bool(
            re.search(r"\b(?:no|not|never|don't|wrong)\b", clean, re.I)
        ),
        "contains_question": "?" in clean,
    }


def _database():
    from memory.database import mongo_db

    return mongo_db


def _save_event(document: dict) -> None:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import save_learning_event

        save_learning_event(document)
        return
    _database().learning_events.update_one(
        {"_id": document["id"], "owner_id": document["owner_id"]},
        {"$setOnInsert": {"_id": document["id"], **document}},
        upsert=True,
    )


def list_learning_events(owner_id: str, source: str | None = None) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_learning_events as local_list

        return local_list(str(owner_id), source)
    query: dict[str, Any] = {"owner_id": str(owner_id)}
    if source:
        query["source"] = source
    return list(
        _database().learning_events.find(query).sort("created_at", 1).limit(500)
    )


def _save_candidate(document: dict) -> dict:
    if document.get("status") not in _CANDIDATE_STATUSES:
        raise ValueError("Unknown learning-candidate status")
    document["updated_at"] = _iso()
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import save_learning_candidate

        return save_learning_candidate(document)
    existing = _database().learning_candidates.find_one(
        {"owner_id": document["owner_id"], "fingerprint": document["fingerprint"]}
    )
    if existing and existing.get("id") != document.get("id"):
        return existing
    _database().learning_candidates.update_one(
        {"_id": document["id"], "owner_id": document["owner_id"]},
        {"$set": {"_id": document["id"], **document}},
        upsert=True,
    )
    return document


def list_learning_candidates(owner_id: str, status: str | None = None) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_learning_candidates as local_list

        return local_list(str(owner_id), status)
    query: dict[str, Any] = {"owner_id": str(owner_id)}
    if status:
        query["status"] = status
    return list(
        _database().learning_candidates.find(query).sort("updated_at", 1).limit(500)
    )


def _candidate(owner_id: str, candidate_id: str) -> dict:
    match = next(
        (
            item
            for item in list_learning_candidates(str(owner_id))
            if str(item.get("id") or item.get("_id")) == str(candidate_id)
        ),
        None,
    )
    if match is None:
        raise KeyError("Learning candidate was not found for this owner")
    return match


def _save_config(document: dict) -> None:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import save_adaptive_config

        save_adaptive_config(document)
        return
    _database().adaptive_config_versions.update_one(
        {"_id": document["id"], "owner_id": document["owner_id"]},
        {"$set": {"_id": document["id"], **document}},
        upsert=True,
    )


def list_adaptive_configs(owner_id: str, config_key: str | None = None) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_adaptive_configs

        return list_adaptive_configs(str(owner_id), config_key)
    query: dict[str, Any] = {"owner_id": str(owner_id)}
    if config_key:
        query["config_key"] = config_key
    return list(
        _database().adaptive_config_versions.find(query).sort("version", 1).limit(500)
    )


def record_learning_event(
    owner_id: str,
    source: str,
    *,
    text: str = "",
    metadata: Mapping[str, Any] | None = None,
    event_id: str | None = None,
) -> dict:
    """Record a redacted outcome signal; raw conversation text is never stored."""
    if source not in EVENT_SOURCES:
        raise ValueError(f"Unsupported learning-event source: {source}")
    if not owner_id:
        raise ValueError("A learning event requires an owner")
    document = {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "owner_id": str(owner_id),
        "source": source,
        "features": _content_features(text),
        "metadata": _json_safe(dict(metadata or {})),
        "created_at": _iso(),
    }
    _save_event(document)
    return document


def _validate_session_value(setting: str, value: Any) -> Any:
    if setting == "response_length":
        normalized = str(value).casefold()
        aliases = {"short": "concise", "brief": "concise", "verbose": "detailed"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"concise", "balanced", "detailed"}:
            raise ValueError(
                "Session response length must be concise, balanced, or detailed"
            )
        return normalized
    if setting in {"active_topic", "task_constraints"}:
        clean = " ".join(str(value).split())[:200]
        if not clean:
            raise ValueError(f"Session {setting} cannot be empty")
        if setting == "task_constraints" and _authority_expansion(clean):
            raise PermissionError("Session constraints may not change authority")
        return clean
    if setting == "device_reference":
        if not isinstance(value, Mapping):
            raise ValueError(
                "A temporary device reference must contain alias and target"
            )
        alias = " ".join(str(value.get("alias", "")).split())[:60]
        target = " ".join(str(value.get("target", "")).split())[:100]
        if not alias or not target:
            raise ValueError("A temporary device reference requires alias and target")
        return {"alias": alias, "target": target}
    raise ValueError(f"Unsupported session adaptation: {setting}")


def set_session_adaptation(
    owner_id: str,
    channel: str,
    setting: str,
    value: Any,
    *,
    ttl_minutes: int | None = None,
) -> dict:
    """Apply Level 0 adaptation inside one session with automatic expiry."""
    from memory.session_store import get_session_manager

    clean = _validate_session_value(setting, value)
    ttl = max(1, min(int(ttl_minutes or _SESSION_TTL_MINUTES), 1440))
    event = record_learning_event(
        str(owner_id),
        "session_adaptation",
        metadata={"channel": channel, "setting": setting, "ttl_minutes": ttl},
    )
    manager = get_session_manager()
    metadata = manager.get_metadata(str(channel), str(owner_id))
    state = dict(metadata.get(_SESSION_KEY) or {})
    values = dict(state.get("values") or {})
    values[setting] = {
        "value": clean,
        "event_id": event["id"],
        "created_at": _iso(),
        "expires_at": _iso(_now() + timedelta(minutes=ttl)),
    }
    state = {"level": 0, "values": values, "updated_at": _iso()}
    manager.set_metadata(str(channel), str(owner_id), _SESSION_KEY, state)
    return state


def get_session_adaptation(owner_id: str, channel: str) -> dict[str, Any]:
    from memory.session_store import get_session_manager

    manager = get_session_manager()
    metadata = manager.get_metadata(str(channel), str(owner_id))
    state = dict(metadata.get(_SESSION_KEY) or {})
    values = dict(state.get("values") or {})
    now = _now()
    active = {
        key: item
        for key, item in values.items()
        if isinstance(item, Mapping)
        and (_parse_time(item.get("expires_at")) or now - timedelta(seconds=1)) > now
    }
    if active != values:
        state = {"level": 0, "values": active, "updated_at": _iso()}
        manager.set_metadata(str(channel), str(owner_id), _SESSION_KEY, state)
    return {key: item.get("value") for key, item in active.items()}


def capture_session_adaptation(owner_id: str, channel: str, text: str) -> dict | None:
    """Capture only language explicitly scoped to the current session."""
    length = _SESSION_LENGTH.search(str(text))
    if length:
        return set_session_adaptation(
            owner_id, channel, "response_length", length.group(1)
        )
    alias = _SESSION_ALIAS.search(str(text))
    if alias:
        return set_session_adaptation(
            owner_id,
            channel,
            "device_reference",
            {"alias": alias.group(1), "target": alias.group(2)},
        )
    topic = _SESSION_TOPIC.search(str(text))
    if topic:
        return set_session_adaptation(owner_id, channel, "active_topic", topic.group(1))
    constraint = _SESSION_CONSTRAINT.search(str(text))
    if constraint:
        return set_session_adaptation(
            owner_id, channel, "task_constraints", constraint.group(1)
        )
    return None


def _authority_expansion(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _AUTHORITY.search(str(key)):
                return True
            if _authority_expansion(item):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_authority_expansion(item) for item in value)
    return bool(_AUTHORITY.search(str(value))) if isinstance(value, str) else False


def create_learning_candidate(
    owner_id: str,
    *,
    level: int | LearningLevel,
    exact_behavior: str,
    problem: str,
    supporting_event_ids: list[str],
    counterexamples: list[str],
    expected_metric_improvement: Mapping[str, Any],
    risk: str,
    expires_at: datetime | str,
    rollback_action: str,
    proposed_change: Mapping[str, Any],
    affected_owners: list[str] | None = None,
    automatic_rollback_threshold: float = -0.02,
) -> dict:
    """Create a deduplicated, non-active learning proposal with full provenance."""
    resolved_level = LearningLevel(int(level))
    if resolved_level < LearningLevel.INFERRED_PATTERN:
        raise ValueError("Levels 0 and 1 are applied through their dedicated APIs")
    if not all(
        [
            exact_behavior.strip(),
            problem.strip(),
            supporting_event_ids,
            expected_metric_improvement,
            risk.strip(),
            rollback_action.strip(),
            proposed_change,
        ]
    ):
        raise ValueError(
            "Learning candidates require behavior, evidence, metrics, risk, and rollback"
        )
    owners = list(dict.fromkeys(str(item) for item in (affected_owners or [owner_id])))
    if owners != [str(owner_id)]:
        raise PermissionError("Adaptive candidates must remain scoped to one owner")
    if _authority_expansion(proposed_change):
        raise PermissionError(
            "Learning may not expand permissions, roles, or authority"
        )
    owner_events = {item["id"]: item for item in list_learning_events(str(owner_id))}
    evidence_ids = list(dict.fromkeys(str(item) for item in supporting_event_ids))
    if any(event_id not in owner_events for event_id in evidence_ids):
        raise ValueError("Every supporting event must exist and belong to the owner")
    sufficient = len(evidence_ids) >= (
        _LEVEL2_MIN_EVIDENCE if resolved_level == LearningLevel.INFERRED_PATTERN else 1
    )
    expiry = _parse_time(expires_at)
    if expiry is None or expiry <= _now():
        raise ValueError("A learning candidate requires a future expiry")
    fingerprint_payload = {
        "owner_id": str(owner_id),
        "level": int(resolved_level),
        "behavior": exact_behavior.strip(),
        "change": _json_safe(dict(proposed_change)),
    }
    fingerprint = _hash(json.dumps(fingerprint_payload, sort_keys=True, default=str))
    existing = next(
        (
            item
            for item in list_learning_candidates(str(owner_id))
            if item.get("fingerprint") == fingerprint
        ),
        None,
    )
    if existing:
        merged_evidence = list(
            dict.fromkeys([*existing.get("supporting_event_ids", []), *evidence_ids])
        )
        existing["supporting_event_ids"] = merged_evidence
        existing["counterexamples"] = list(
            dict.fromkeys(
                [
                    *existing.get("counterexamples", []),
                    *[_hash(str(item)) for item in counterexamples[:50]],
                ]
            )
        )[-50:]
        if (
            existing.get("status") == "insufficient_evidence"
            and len(merged_evidence) >= _LEVEL2_MIN_EVIDENCE
        ):
            existing["status"] = "pending_evaluation"
        return _save_candidate(existing)
    now = _iso()
    document = {
        "id": f"cand_{uuid.uuid4().hex}",
        "owner_id": str(owner_id),
        "level": int(resolved_level),
        "exact_behavior": exact_behavior.strip()[:500],
        "problem": problem.strip()[:500],
        "supporting_event_ids": evidence_ids,
        "counterexamples": [_hash(str(item)) for item in counterexamples[:50]],
        "affected_owners": owners,
        "expected_metric_improvement": _json_safe(dict(expected_metric_improvement)),
        "risk": risk.strip()[:120],
        "expires_at": expiry.isoformat(),
        "rollback_action": rollback_action.strip()[:300],
        "automatic_rollback_threshold": float(automatic_rollback_threshold),
        "proposed_change": _json_safe(dict(proposed_change)),
        "fingerprint": fingerprint,
        "status": "pending_evaluation" if sufficient else "insufficient_evidence",
        "evaluation": {},
        "shadow_observations": [],
        "canary_observations": [],
        "created_at": now,
        "updated_at": now,
    }
    return _save_candidate(document)


def propose_low_risk_candidate(
    owner_id: str,
    *,
    behavior: str,
    problem: str,
    event_ids: list[str],
    config_key: str,
    value: Any,
    metric: str,
) -> dict:
    return create_learning_candidate(
        owner_id,
        level=LearningLevel.INFERRED_PATTERN,
        exact_behavior=behavior,
        problem=problem,
        supporting_event_ids=event_ids,
        counterexamples=[],
        expected_metric_improvement={"metric": metric, "minimum_delta": 0.01},
        risk="low",
        expires_at=_now() + timedelta(days=45),
        rollback_action=f"Restore the previous {config_key} configuration version",
        proposed_change={
            "config_key": config_key,
            "value": value,
            "consequential": False,
        },
    )


def reject_candidate(owner_id: str, candidate_id: str, reason: str) -> dict:
    document = _candidate(owner_id, candidate_id)
    document["status"] = "rejected"
    document["rejection"] = {"reason": str(reason)[:300], "created_at": _iso()}
    return _save_candidate(document)


def run_offline_evaluation(
    owner_id: str,
    candidate_id: str,
    evaluator: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
) -> dict:
    """Run all required gates through an injected offline evaluator."""
    document = _candidate(owner_id, candidate_id)
    if document["status"] not in {"pending_evaluation", "evaluation_failed"}:
        raise ValueError("Candidate is not awaiting offline evaluation")
    if (_parse_time(document.get("expires_at")) or _now()) <= _now():
        document["status"] = "expired"
        return _save_candidate(document)
    results: dict[str, dict] = {}
    for stage in _EVALUATION_STAGES:
        raw = dict(evaluator(stage, dict(document)))
        results[stage] = {
            "passed": bool(raw.get("passed")),
            "safety_passed": bool(raw.get("safety_passed", True)),
            "score": float(raw.get("score", 0.0)),
            "baseline_score": float(raw.get("baseline_score", 0.0)),
            "metric_delta": float(raw.get("metric_delta", 0.0)),
            "confidence_low": float(raw.get("confidence_low", 0.0)),
            "case_count": max(0, int(raw.get("case_count", 0))),
        }
    expected = document.get("expected_metric_improvement", {})
    minimum_delta = float(expected.get("minimum_delta", 0.0))
    full = results["full"]
    passed = (
        all(item["passed"] and item["safety_passed"] for item in results.values())
        and full["metric_delta"] >= minimum_delta
        and full["confidence_low"] > 0.0
        and results["adversarial"]["case_count"] > 0
    )
    document["evaluation"] = {
        "stages": results,
        "passed": passed,
        "minimum_delta": minimum_delta,
        "completed_at": _iso(),
    }
    if not passed:
        document["status"] = "evaluation_failed"
    elif int(document["level"]) == int(LearningLevel.CODE_OR_SKILL):
        document["status"] = "awaiting_human_approval"
    else:
        document["status"] = "shadow"
    return _save_candidate(document)


def record_shadow_observation(
    owner_id: str,
    candidate_id: str,
    *,
    live_output: str,
    candidate_output: str,
    disagreed: bool,
    safety_regression: bool = False,
) -> dict:
    document = _candidate(owner_id, candidate_id)
    if document["status"] != "shadow":
        raise ValueError("Only evaluated shadow candidates may receive shadow traces")
    observations = list(document.get("shadow_observations", []))
    observations.append(
        {
            "live_hash": _hash(str(live_output)),
            "candidate_hash": _hash(str(candidate_output)),
            "disagreed": bool(disagreed),
            "safety_regression": bool(safety_regression),
            "created_at": _iso(),
        }
    )
    document["shadow_observations"] = observations[-500:]
    return _save_candidate(document)


def _shadow_ready(document: Mapping[str, Any], minimum: int) -> bool:
    observations = list(document.get("shadow_observations", []))
    if len(observations) < minimum:
        return False
    if any(item.get("safety_regression") for item in observations):
        return False
    disagreement_rate = sum(bool(item.get("disagreed")) for item in observations) / len(
        observations
    )
    return disagreement_rate <= 0.1


def _activate_config(document: dict, *, status: str) -> dict:
    change = document.get("proposed_change", {})
    config_key = str(change.get("config_key", "")).strip()
    if not config_key or "value" not in change:
        raise ValueError("Promotable candidates require a config_key and value")
    if _authority_expansion(change):
        raise PermissionError("Learning may not expand authority")
    existing = list_adaptive_configs(document["owner_id"], config_key)
    version = max((int(item.get("version", 0)) for item in existing), default=0) + 1
    prior = next(
        (item for item in reversed(existing) if item.get("status") == "active"), None
    )
    if status == "active" and prior:
        prior["status"] = "superseded"
        _save_config(prior)
    config = {
        "id": f"{document['owner_id']}:{_hash(config_key)[:12]}:v{version}",
        "owner_id": document["owner_id"],
        "candidate_id": document["id"],
        "level": document["level"],
        "config_key": config_key,
        "value": change["value"],
        "version": version,
        "prior_version": prior.get("version") if prior else None,
        "status": status,
        "metric_delta": document.get("evaluation", {})
        .get("stages", {})
        .get("full", {})
        .get("metric_delta"),
        "rollback_threshold": document["automatic_rollback_threshold"],
        "rollback_command": f"/learning rollback {config_key}",
        "provenance_event_ids": list(document["supporting_event_ids"]),
        "created_at": _iso(),
    }
    _save_config(config)
    return config


def promote_low_risk_candidate(
    owner_id: str,
    candidate_id: str,
    *,
    human_approved: bool,
    minimum_shadow_observations: int | None = None,
) -> dict:
    document = _candidate(owner_id, candidate_id)
    if int(document["level"]) != int(LearningLevel.INFERRED_PATTERN):
        raise ValueError("This promotion path is only for Level 2 candidates")
    if not human_approved:
        raise PermissionError("An inferred pattern requires explicit owner approval")
    minimum = (
        _SHADOW_MIN_OBSERVATIONS
        if minimum_shadow_observations is None
        else max(1, int(minimum_shadow_observations))
    )
    if document["status"] != "shadow" or not _shadow_ready(document, minimum):
        raise ValueError("Candidate has not completed safe shadow observation")
    config = _activate_config(document, status="active")
    document["status"] = "promoted"
    document["promoted_config_id"] = config["id"]
    document["human_approved_at"] = _iso()
    _save_candidate(document)
    return config


def start_canary(
    owner_id: str,
    candidate_id: str,
    *,
    owner_opted_in: bool,
    minimum_shadow_observations: int | None = None,
) -> dict:
    document = _candidate(owner_id, candidate_id)
    if int(document["level"]) != int(LearningLevel.OPTIMIZATION):
        raise ValueError("Only Level 3 optimizations use runtime canaries")
    if not owner_opted_in:
        raise PermissionError("A canary requires an opted-in owner")
    minimum = (
        _SHADOW_MIN_OBSERVATIONS
        if minimum_shadow_observations is None
        else max(1, int(minimum_shadow_observations))
    )
    if document["status"] != "shadow" or not _shadow_ready(document, minimum):
        raise ValueError("Candidate has not completed safe shadow observation")
    config = _activate_config(document, status="canary")
    document["status"] = "canary"
    document["canary_config_id"] = config["id"]
    document["canary_owner_opted_in_at"] = _iso()
    _save_candidate(document)
    return config


def record_canary_observation(
    owner_id: str,
    candidate_id: str,
    *,
    metric_delta: float,
    safety_regression: bool = False,
) -> dict:
    document = _candidate(owner_id, candidate_id)
    if document["status"] != "canary":
        raise ValueError("Candidate is not in canary mode")
    observations = list(document.get("canary_observations", []))
    observations.append(
        {
            "metric_delta": float(metric_delta),
            "safety_regression": bool(safety_regression),
            "created_at": _iso(),
        }
    )
    document["canary_observations"] = observations[-500:]
    threshold = float(document["automatic_rollback_threshold"])
    if safety_regression or float(metric_delta) < threshold:
        rollback_configuration(
            str(owner_id),
            str(document["proposed_change"]["config_key"]),
            reason="automatic canary rollback threshold reached",
        )
        document["status"] = "rolled_back"
        document["automatic_rollback_at"] = _iso()
    return _save_candidate(document)


def promote_canary(
    owner_id: str,
    candidate_id: str,
    *,
    minimum_canary_observations: int | None = None,
) -> dict:
    document = _candidate(owner_id, candidate_id)
    observations = list(document.get("canary_observations", []))
    minimum = (
        _CANARY_MIN_OBSERVATIONS
        if minimum_canary_observations is None
        else max(1, int(minimum_canary_observations))
    )
    threshold = float(document["automatic_rollback_threshold"])
    if (
        document["status"] != "canary"
        or len(observations) < minimum
        or any(item.get("safety_regression") for item in observations)
        or sum(float(item["metric_delta"]) for item in observations) / len(observations)
        < max(0.0, threshold)
    ):
        raise ValueError("Canary has not met its safe promotion criteria")
    configs = list_adaptive_configs(
        str(owner_id), str(document["proposed_change"]["config_key"])
    )
    canary = next(
        (
            item
            for item in configs
            if item.get("id") == document.get("canary_config_id")
        ),
        None,
    )
    if canary is None:
        raise RuntimeError("Canary configuration is missing")
    for item in configs:
        if item.get("status") == "active":
            item["status"] = "superseded"
            _save_config(item)
    canary["status"] = "active"
    canary["promoted_at"] = _iso()
    _save_config(canary)
    document["status"] = "promoted"
    document["promoted_config_id"] = canary["id"]
    _save_candidate(document)
    return canary


def stage_code_candidate(
    owner_id: str,
    candidate_id: str,
    *,
    isolated_branch: str,
    generated_tests_passed: bool,
    human_tests_passed: bool,
    security_review_passed: bool,
    human_approved: bool,
) -> dict:
    """Record Level 4 readiness without merging, pushing, or deploying anything."""
    document = _candidate(owner_id, candidate_id)
    if int(document["level"]) != int(LearningLevel.CODE_OR_SKILL):
        raise ValueError("This gate applies only to Level 4 candidates")
    if document["status"] != "awaiting_human_approval":
        raise ValueError("Code candidate has not passed offline evaluation")
    branch = str(isolated_branch).strip()
    if not branch or branch in {"main", "master", "production", "prod"}:
        raise ValueError("Code changes require a non-production isolated branch")
    gates = {
        "generated_tests_passed": bool(generated_tests_passed),
        "human_tests_passed": bool(human_tests_passed),
        "security_review_passed": bool(security_review_passed),
        "human_approved": bool(human_approved),
    }
    if not all(gates.values()):
        raise PermissionError(
            "Level 4 staging requires every test, review, and approval gate"
        )
    if _authority_expansion(document.get("proposed_change", {})):
        raise PermissionError("A learned code change may not expand authority")
    document["status"] = "staged"
    document["staging"] = {**gates, "branch": branch, "staged_at": _iso()}
    document["deployment_allowed"] = False
    document["merge_allowed"] = False
    return _save_candidate(document)


def rollback_configuration(owner_id: str, config_key: str, *, reason: str) -> dict:
    """One-command rollback to the most recent superseded active version."""
    configs = list_adaptive_configs(str(owner_id), str(config_key))
    current = next(
        (
            item
            for item in reversed(configs)
            if item.get("status") in {"active", "canary"}
        ),
        None,
    )
    if current is None:
        raise KeyError("No active adaptive configuration exists for this key")
    current["status"] = "rolled_back"
    current["rollback_reason"] = str(reason)[:300]
    current["rolled_back_at"] = _iso()
    _save_config(current)
    previous = next(
        (
            item
            for item in reversed(configs)
            if item.get("version") == current.get("prior_version")
            or (
                int(item.get("version", 0)) < int(current.get("version", 0))
                and item.get("status") == "superseded"
            )
        ),
        None,
    )
    if previous:
        previous["status"] = "active"
        previous["restored_at"] = _iso()
        _save_config(previous)
    for candidate in list_learning_candidates(str(owner_id)):
        if candidate.get("id") == current.get("candidate_id"):
            candidate["status"] = "rolled_back"
            _save_candidate(candidate)
            break
    return {"rolled_back": current, "restored": previous}


def disable_configuration(owner_id: str, config_key: str, *, reason: str) -> int:
    """Disable every live version for an explicit preference reset."""
    disabled = 0
    candidate_ids: set[str] = set()
    for config in list_adaptive_configs(str(owner_id), str(config_key)):
        if config.get("status") not in {"active", "canary"}:
            continue
        config["status"] = "rolled_back"
        config["rollback_reason"] = str(reason)[:300]
        config["rolled_back_at"] = _iso()
        _save_config(config)
        disabled += 1
        if config.get("candidate_id"):
            candidate_ids.add(str(config["candidate_id"]))
    for candidate in list_learning_candidates(str(owner_id)):
        if str(candidate.get("id")) in candidate_ids:
            candidate["status"] = "rolled_back"
            _save_candidate(candidate)
    return disabled


def get_active_configuration(
    owner_id: str, config_key: str, *, consequential: bool = False
) -> Any | None:
    configs = list_adaptive_configs(str(owner_id), str(config_key))
    active = next(
        (item for item in reversed(configs) if item.get("status") == "active"), None
    )
    if active is None:
        return None
    if consequential and int(active.get("level", 0)) == int(
        LearningLevel.INFERRED_PATTERN
    ):
        return None
    return active.get("value")


def get_active_configurations(
    owner_id: str, *, consequential: bool = False
) -> dict[str, Any]:
    """Return one newest active value per key with a single repository read."""
    active: dict[str, Any] = {}
    for item in reversed(list_adaptive_configs(str(owner_id))):
        key = str(item.get("config_key") or "")
        if not key or key in active or item.get("status") != "active":
            continue
        if consequential and int(item.get("level", 0)) == int(
            LearningLevel.INFERRED_PATTERN
        ):
            continue
        active[key] = item.get("value")
    return active


def handle_learning_command(owner_id: str, text: str) -> str | None:
    match = _LEARNING_COMMAND.fullmatch(str(text).strip())
    if not match:
        return None
    action = (match.group(1) or "inspect").casefold()
    argument = (match.group(2) or "").strip()
    if action == "events":
        events = list_learning_events(str(owner_id))[-50:]
        return json.dumps({"owner_id": str(owner_id), "events": events}, indent=2)
    if action == "inspect":
        return json.dumps(
            {
                "owner_id": str(owner_id),
                "candidates": list_learning_candidates(str(owner_id))[-50:],
                "configurations": list_adaptive_configs(str(owner_id)),
            },
            indent=2,
            default=str,
        )
    if not argument:
        return f"Use `/learning {action} <candidate-or-config>`."
    if action == "reject":
        reject_candidate(str(owner_id), argument, "rejected by owner")
        return f"Rejected learning candidate `{argument}`. It remains available for analysis."
    result = rollback_configuration(
        str(owner_id), argument, reason="owner requested one-command rollback"
    )
    restored = result.get("restored")
    return f"Rolled back `{argument}`" + (
        f" to version {restored['version']}." if restored else "."
    )
