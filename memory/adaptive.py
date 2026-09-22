"""Guarded long-term learning, declarative abilities, and helpful predictions.

The local LLM supplies neural pattern recognition. This module supplies the
non-neural safety boundary: provenance, confidence thresholds, expiry, consent,
rate limits, and a strict ban on generated-code execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re
from typing import Any, Mapping, Optional

from memory.database import mongo_db
from memory.hierarchy import (
    default_importance,
    memory_stats,
    memory_tier,
    rank_memories,
)

logger = logging.getLogger(__name__)

_MEMORY_MIN_CONFIDENCE = float(os.getenv("ADAPTIVE_MEMORY_MIN_CONFIDENCE", "0.8"))
_PREDICTION_MIN_CONFIDENCE = float(
    os.getenv("PROACTIVE_PREDICTION_MIN_CONFIDENCE", "0.82")
)
_INFERRED_TTL_DAYS = int(os.getenv("ADAPTIVE_INFERRED_TTL_DAYS", "45"))
_TEMPORARY_TTL_DAYS = int(os.getenv("ADAPTIVE_TEMPORARY_TTL_DAYS", "14"))

# Keep Mongo recall reads lean. Evidence is intentionally excluded because the
# prompt builder uses provenance metadata, not the original conversation text.
_RETRIEVAL_PROJECTION = {
    "_id": 1,
    "id": 1,
    "owner_id": 1,
    "internal_id": 1,
    "kind": 1,
    "tier": 1,
    "key": 1,
    "value": 1,
    "summary": 1,
    "tags": 1,
    "source": 1,
    "source_message_id": 1,
    "source_channel": 1,
    "confidence": 1,
    "importance": 1,
    "contradicts": 1,
    "status": 1,
    "sensitivity": 1,
    "created_at": 1,
    "updated_at": 1,
    "last_confirmed_at": 1,
    "last_seen_at": 1,
    "expires_at": 1,
    "active": 1,
    "confirmation_count": 1,
}

_KINDS = frozenset(
    {
        "identity",
        "biography",
        "preference",
        "routine",
        "project",
        "relationship",
        "temporary_context",
        "assistant_setting",
        "hypothesis",
        "episode",
    }
)
_SENSITIVE = re.compile(
    r"\b(?:password|passcode|api[_ -]?key|secret|private[_ -]?key|credit[_ -]?card|"
    r"access[_ -]?token|auth[_ -]?token|bearer token|credential|session cookie|"
    r"recovery code|two.factor|2fa|bank[_ -]?account|social security|passport number|"
    r"medical record|diagnosis)\b|-----BEGIN [A-Z ]+PRIVATE KEY-----|"
    r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b",
    re.I,
)
_DO_NOT_REMEMBER = re.compile(
    r"\b(?:do not|don't|dont|never)\s+(?:save|store|remember|memorize)\b|\boff the record\b",
    re.I,
)


def _memory_kind(key: str, source: str) -> str:
    if source != "explicit_user_statement":
        return "hypothesis"
    key = key.casefold()
    if key in {"name", "pronouns", "language", "timezone"}:
        return "identity"
    if any(part in key for part in ("prefer", "favorite", "like", "diet", "style")):
        return "preference"
    if any(part in key for part in ("routine", "habit", "wake", "sleep", "hours")):
        return "routine"
    if any(part in key for part in ("project", "goal", "task")):
        return "project"
    if any(part in key for part in ("friend", "partner", "family", "relationship")):
        return "relationship"
    if any(part in key for part in ("current", "travel", "trip", "temporary", "visit")):
        return "temporary_context"
    if any(part in key for part in ("assistant", "reminder", "verbosity", "proactive")):
        return "assistant_setting"
    return "biography"


def _normalize_stored_memory(document: dict, internal_id: str) -> dict:
    """Backfill safe metadata on pre-hierarchy records without changing values."""
    item = dict(document)
    updates: dict[str, Any] = {}
    source = str(item.get("source") or "legacy_import")
    kind = str(item.get("kind") or "").casefold()
    if kind not in _KINDS:
        kind = _memory_kind(str(item.get("key", "")), source)
        updates["kind"] = kind
    if str(item.get("tier") or "").casefold() not in {
        "core",
        "episodic",
        "archival",
    }:
        updates["tier"] = memory_tier({"kind": kind})
    if not item.get("source"):
        updates["source"] = source
    if not item.get("owner_id"):
        updates["owner_id"] = str(internal_id)
    if not item.get("internal_id"):
        updates["internal_id"] = str(internal_id)
    status = str(item.get("status") or "").casefold()
    if status not in {
        "verified",
        "recorded",
        "hypothesis",
        "pending_confirmation",
        "corrected",
        "superseded",
        "rolled_back",
    }:
        updates["status"] = (
            "recorded"
            if kind == "episode"
            else "verified" if source == "explicit_user_statement" else "hypothesis"
        )
    if item.get("confidence") is None:
        updates["confidence"] = (
            1.0 if source == "explicit_user_statement" else _MEMORY_MIN_CONFIDENCE
        )
    if item.get("importance") is None:
        updates["importance"] = default_importance(kind)
    if item.get("active") is None:
        updates["active"] = True
    if item.get("confirmation_count") is None:
        updates["confirmation_count"] = 1
    if item.get("sensitivity") is None:
        updates["sensitivity"] = "ordinary"
    if item.get("contradicts") is None:
        updates["contradicts"] = []
    if not updates:
        return item

    item.update(updates)
    memory_id = item.get("id") or item.get("_id")
    if memory_id is None:
        return item
    try:
        if not os.getenv("MONGODB_URI"):
            from memory.local_store import update_adaptive_memory

            update_adaptive_memory(str(memory_id), str(internal_id), updates)
        else:
            mongo_db.adaptive_memories.update_one(
                {
                    "_id": document.get("_id"),
                    "$or": [
                        {"owner_id": str(internal_id)},
                        {"internal_id": str(internal_id)},
                    ],
                },
                {"$set": updates},
            )
    except Exception as exc:
        # Retrieval remains available with the normalized copy even if a
        # persistence backend is temporarily read-only.
        logger.debug("Legacy memory metadata backfill skipped: %s", exc)
    return item


def _memory_enabled(internal_id: str, channel: str | None = None) -> bool:
    from memory.repositories import get_repositories

    profile = get_repositories().profiles.get(str(internal_id))
    return profile.get("memory_enabled", True) and channel not in set(
        profile.get("memory_paused_channels", [])
    )


def _unified_memory_service():
    from memory.service import get_memory_service

    return get_memory_service(
        mongo_database=mongo_db if os.getenv("MONGODB_URI") else None
    )


def _all_owner_memories(internal_id: str) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_adaptive_memories

        rows = list_adaptive_memories(str(internal_id))
    else:
        rows = list(
            mongo_db.adaptive_memories.find(
                {
                    "$or": [
                        {"owner_id": str(internal_id)},
                        {"internal_id": str(internal_id)},
                    ]
                }
            ).limit(500)
        )
    return [_normalize_stored_memory(item, str(internal_id)) for item in rows]


_UNSAFE_ACTIONS = re.compile(
    r"\b(?:buy|purchase|pay|transfer|invest|trade|delete|remove|send|post|publish|"
    r"message|email|call|book|cancel|install|execute|run command|change password|"
    r"medical treatment|diagnos|legal filing)\b",
    re.IGNORECASE,
)
_TEACH_PATTERN = re.compile(
    r"\bwhen i (?:say|ask|mention)\s+['\"]?(.{2,80}?)['\"]?\s*,\s*"
    r"(?:please\s+)?(.{3,300})$",
    re.IGNORECASE,
)
_APPROVE_PATTERN = re.compile(r"^/?approve\s+skill\s+([a-z0-9_-]{2,64})$", re.I)
_REJECT_PATTERN = re.compile(r"^/?reject\s+skill\s+([a-z0-9_-]{2,64})$", re.I)
_MEMORY_COMMAND = re.compile(
    r"^/?memory(?:\s+(?P<action>inspect|stats|search|timeline|why|forget|export|pause|resume|disable|correct|confirm|rollback))?(?:\s+(?P<argument>.*))?$",
    re.I,
)
_NATURAL_MEMORY = re.compile(
    r"^(?:what do you remember(?: about me)?|forget that|forget everything|"
    r"do not learn from this chat|don't learn from this chat|resume learning from this chat)$",
    re.I,
)
_NATURAL_MEMORY_SEARCH = re.compile(r"^what do you remember about\s+(.+?)[?.!]*$", re.I)
_EPISODE_SIGNAL = re.compile(
    r"\b(?:remember this|keep track of|for future reference|we (?:decided|agreed)|"
    r"the plan is|next step|from now on|my project|our project|working on|"
    r"i (?:want|need|plan) to|(?:can|could) we (?:build|create|make)|let's (?:build|create)|"
    r"milestone|shipped|deployed|resolved|fixed)\b",
    re.I,
)
_EPISODE_TTL_DAYS = int(os.getenv("MEMORY_EPISODE_TTL_DAYS", "180"))
_PROFILE_RUNTIME_KEYS = frozenset(
    {
        "memory_enabled",
        "memory_paused_channels",
        "contact_channels",
        "proactive_messaging_enabled",
        "proactive_interval_hours",
        "proactive_predictions_enabled",
        "proactive_quiet_hours",
        "proactive_daily_max",
        "proactive_weekly_max",
        "proactive_topic_cooldown_hours",
        "proactive_avoid_topics",
        "proactive_rejection_count",
        "roles",
        "permissions",
        "is_master",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]
    return clean or hashlib.sha256(text.encode()).hexdigest()[:12]


def is_safe_proposed_action(text: str) -> bool:
    """Predictions may suggest low-risk help, never perform consequential work."""
    return bool(text.strip()) and not _UNSAFE_ACTIONS.search(text)


def _sync_core_profile(internal_id: str, key: str, value: Any) -> None:
    """Mirror a verified fact into the small always-available profile."""
    if os.getenv("MONGODB_URI"):
        mongo_db.user_profiles.update_one(
            {"_id": str(internal_id)},
            {
                "$set": {f"facts.{key}": value},
                "$currentDate": {"last_updated": True},
            },
            upsert=True,
        )
        return
    from memory.users import UserManager

    UserManager.update_user_profile(str(internal_id), {str(key): value})


def record_memories(
    internal_id: str,
    facts: dict[str, Any],
    evidence: str,
    source: str = "explicit_user_statement",
    *,
    source_message_id: str = "",
    source_channel: str = "unknown",
) -> list[dict]:
    """Persist facts through the unified policy and repository boundary."""
    service = _unified_memory_service()
    unified_results: list[dict] = []
    if not internal_id or not facts:
        return unified_results
    if not _memory_enabled(internal_id, source_channel) or _DO_NOT_REMEMBER.search(
        evidence
    ):
        return unified_results
    for key, value in facts.items():
        kind = _memory_kind(str(key), source)
        record = service.remember(
            str(internal_id),
            predicate=str(key),
            value=value,
            type=kind,
            source=source,
            source_turn=source_message_id,
            source_channel=source_channel,
            evidence=evidence,
        )
        if record is None:
            continue
        document = record.to_document()
        if document["active"] and document["status"] == "verified":
            _sync_core_profile(str(internal_id), str(key), value)
        unified_results.append(document)
    return unified_results


def record_conversation_episode(
    internal_id: str,
    user_message: str,
    *,
    source_message_id: str = "",
    source_channel: str = "unknown",
) -> Optional[dict]:
    """Store only a salient user-authored decision, goal, or milestone.

    Episodes are bounded excerpts, not model-authored summaries, so an assistant
    hallucination cannot silently become long-term truth.
    """
    clean = re.sub(r"\s+", " ", str(user_message or "")).strip()
    if (
        not internal_id
        or len(clean.split()) < 5
        or not _EPISODE_SIGNAL.search(clean)
        or _SENSITIVE.search(clean)
        or _DO_NOT_REMEMBER.search(clean)
        or not _memory_enabled(str(internal_id), source_channel)
    ):
        return None
    content = clean[:350]
    from memory.service import MemoryTier

    record = _unified_memory_service().remember(
        str(internal_id),
        predicate=f"episode_{_slug(content[:90])}"[:64],
        value=content,
        type="episode",
        source="explicit_conversation_episode",
        source_turn=source_message_id,
        source_channel=source_channel,
        evidence=content,
        tier=MemoryTier.EPISODIC,
        confidence=0.95,
        valid_until=_now() + timedelta(days=max(1, _EPISODE_TTL_DAYS)),
    )
    return record.to_document() if record else None


def get_relevant_memories(
    internal_id: str,
    query: str,
    limit: int = 8,
    *,
    profile: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Return owner-scoped recall from the unified memory service."""
    if not _memory_enabled(internal_id):
        return []
    service = _unified_memory_service()
    return service.retrieve(
        str(internal_id),
        query,
        limit=limit,
        supplemental_records=service.profile_records(str(internal_id), profile or {}),
    ).as_documents()


def get_pending_memory_conflicts(internal_id: str) -> list[dict]:
    """Return only this owner's unresolved contradictions for confirmation UI."""
    from memory.service import ConfirmationState

    return [
        item.to_document()
        for item in _unified_memory_service().inspect(str(internal_id))
        if item.confirmation_state is ConfirmationState.CONFLICTED
    ][:10]


def propose_learned_ability(internal_id: str, user_message: str) -> Optional[dict]:
    """Draft a typed declarative skill only from an explicit teaching phrase."""
    match = _TEACH_PATTERN.search(user_message.strip())
    if not match:
        return None
    trigger, procedure = (part.strip(" .\"'") for part in match.groups())
    if _UNSAFE_ACTIONS.search(procedure):
        return None
    name = _slug(trigger)
    from memory.learned_skills import draft_skill

    return draft_skill(
        str(internal_id),
        name=name,
        trigger_examples=[trigger],
        procedure=procedure,
        kind="declarative_response",
    )


def handle_adaptive_command(
    internal_id: str, text: str, channel: str | None = None
) -> Optional[str]:
    """Approve/reject pending learned recipes. No generated code is executed."""
    from memory.learned_skills import (
        approve_skill,
        handle_skill_command,
        reject_skill,
    )

    skill_response = handle_skill_command(str(internal_id), text)
    if skill_response is not None:
        return skill_response
    natural_search = _NATURAL_MEMORY_SEARCH.fullmatch(text.strip())
    if natural_search:
        subject = natural_search.group(1).strip()
        if subject.casefold() == "me":
            return _handle_memory_command(str(internal_id), "inspect", "")
        return _handle_memory_command(str(internal_id), "search", subject)
    natural = _NATURAL_MEMORY.fullmatch(text.strip())
    if natural:
        from memory.repositories import get_repositories

        lowered = text.strip().casefold()
        if lowered.startswith("what do you remember"):
            return _handle_memory_command(str(internal_id), "inspect", "")
        if lowered == "forget everything":
            return _handle_memory_command(str(internal_id), "forget", "all")
        if lowered == "forget that":
            return "Tell me which memory to forget, so I don't remove the wrong thing."
        profile = get_repositories().profiles.get(str(internal_id))
        paused = set(profile.get("memory_paused_channels", []))
        if lowered.startswith(("do not", "don't")) and channel:
            paused.add(channel)
            get_repositories().profiles.update(
                str(internal_id), {"memory_paused_channels": sorted(paused)}
            )
            return f"I won’t learn from this {channel} chat. Existing memories are unchanged."
        if channel:
            paused.discard(channel)
            get_repositories().profiles.update(
                str(internal_id), {"memory_paused_channels": sorted(paused)}
            )
            return f"Memory learning resumed for this {channel} chat."
    memory_match = _MEMORY_COMMAND.fullmatch(text.strip())
    if memory_match:
        return _handle_memory_command(
            str(internal_id),
            (memory_match.group("action") or "inspect").casefold(),
            (memory_match.group("argument") or "").strip(),
        )
    for pattern, status in (
        (_APPROVE_PATTERN, "active"),
        (_REJECT_PATTERN, "rejected"),
    ):
        match = pattern.fullmatch(text.strip())
        if not match:
            continue
        name = match.group(1).lower()
        if status == "active":
            result = approve_skill(str(internal_id), name)
            modified = result is not None
            if result and result.get("status") != "active":
                return f"Learned skill `{name}` failed its safety evaluation and was not activated."
        else:
            modified = reject_skill(str(internal_id), name)
        if not modified:
            return f"I could not find a pending learned skill named `{name}`."
        verb = "approved" if status == "active" else "rejected"
        return f"Learned skill `{name}` {verb}."
    return None


def _set_memory_enabled(internal_id: str, enabled: bool) -> None:
    from memory.repositories import get_repositories

    get_repositories().profiles.update(internal_id, {"memory_enabled": enabled})


def _forget_memories(internal_id: str, key: str | None = None) -> int:
    unified_count = _unified_memory_service().forget(
        str(internal_id), predicate=key if key is not None else None
    )
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import delete_adaptive_memories

        return max(unified_count, delete_adaptive_memories(internal_id, key))
    query: dict[str, Any] = {
        "$or": [{"owner_id": internal_id}, {"internal_id": internal_id}]
    }
    if key is not None:
        query["key"] = {"$regex": f"^{re.escape(key)}$", "$options": "i"}
    return max(
        unified_count,
        int(getattr(mongo_db.adaptive_memories.delete_many(query), "deleted_count", 0)),
    )


def _forget_core_profile(internal_id: str, key: str | None = None) -> int:
    from memory.users import UserManager

    if key is not None:
        keys = {key}
    else:
        profile = UserManager.get_user_profile(str(internal_id)) or {}
        keys = {
            item
            for item in profile
            if item not in _PROFILE_RUNTIME_KEYS
            and not item.startswith("_")
            and not item.startswith("proactive_")
            and not item.startswith("last_")
        }
    return UserManager.delete_user_profile_facts(str(internal_id), keys)


def _update_memory(internal_id: str, memory_id: str, updates: dict) -> bool:
    from memory.service import MemoryRecord

    if not os.getenv("MONGODB_URI"):
        from memory.local_store import update_adaptive_memory

        modified = update_adaptive_memory(memory_id, internal_id, updates)
    else:
        result = mongo_db.adaptive_memories.update_one(
            {"_id": memory_id, "owner_id": internal_id}, {"$set": updates}
        )
        modified = bool(getattr(result, "modified_count", 0))
    service = _unified_memory_service()
    current = service.repository.get(str(internal_id), str(memory_id))
    if current is not None:
        document = current.to_document()
        document.update(updates)
        if "status" in updates:
            document["confirmation_state"] = {
                "verified": "confirmed",
                "recorded": "recorded",
                "hypothesis": "candidate",
                "pending_confirmation": "conflicted",
                "corrected": "invalidated",
                "superseded": "invalidated",
                "rolled_back": "invalidated",
            }.get(str(updates["status"]), document["confirmation_state"])
            document["tombstone"] = str(updates["status"]) in {
                "corrected",
                "superseded",
                "rolled_back",
            }
        document["updated_at"] = _now().isoformat()
        service.repository.upsert(MemoryRecord.from_document(document))
        modified = True
    return modified


def _handle_memory_command(internal_id: str, action: str, argument: str) -> str:
    memories = _all_owner_memories(internal_id)
    if action in {"pause", "disable"}:
        _set_memory_enabled(internal_id, False)
        return "Memory is disabled. I will not store or retrieve personal memories."
    if action == "resume":
        _set_memory_enabled(internal_id, True)
        return "Memory is enabled again."
    if action == "export":
        exportable = [
            {k: v for k, v in item.items() if not k.startswith("_")}
            for item in memories
        ]
        return json.dumps(
            {"owner_id": internal_id, "memories": exportable}, default=str, indent=2
        )
    if action == "stats":
        stats = memory_stats(memories)
        tiers = stats["tiers"]
        return (
            f"Memory has {stats['active']} active item(s): {tiers['core']} core, "
            f"{tiers['episodic']} episodic, and {tiers['archival']} archival. "
            f"There are {stats['pending']} pending conflict(s) and "
            f"{stats['expired']} expired item(s)."
        )
    if action == "search":
        if not argument:
            return "Tell me what to search for, for example `/memory search travel`."
        matches = rank_memories(
            argument,
            memories,
            limit=10,
            char_budget=4000,
            explicit_search=True,
            owner_id=internal_id,
        )
        if not matches:
            return f"I couldn't find a relevant memory for `{argument}`."
        lines = [f"Memory matches for `{argument}`:"]
        for item in matches:
            lines.append(
                f"- [{item.get('_memory_tier', 'archival')}] "
                f"{item.get('key')} = {item.get('value')}"
            )
        return "\n".join(lines)
    if action == "timeline":
        ordered = sorted(
            memories, key=lambda item: str(item.get("created_at", "")), reverse=True
        )
        if not ordered:
            return "Your memory timeline is empty."
        return "Your memory timeline:\n" + "\n".join(
            f"- {item.get('created_at')}: {item.get('key')} = {item.get('value')} "
            f"[{item.get('status', 'verified')}]"
            for item in ordered[:50]
        )
    if action == "forget":
        key = None if argument.casefold() in {"", "all", "everything"} else argument
        stored_count = _forget_memories(internal_id, key)
        profile_count = _forget_core_profile(internal_id, key)
        count = max(stored_count, profile_count)
        return f"Forgot {count} memory record(s)."
    if action == "correct":
        match = re.fullmatch(
            r"([a-zA-Z0-9_ -]{1,64})\s*(?:=|to)\s*(.{1,200})", argument
        )
        if not match:
            return "Use `/memory correct key = new value`."
        key, value = match.group(1).strip().replace(" ", "_"), match.group(2).strip()
        if _SENSITIVE.search(f"{key} {value}"):
            return "I did not store that because it appears sensitive."
        for item in memories:
            if str(item.get("key", "")).casefold() == key.casefold():
                _update_memory(
                    internal_id,
                    str(item.get("id") or item.get("_id")),
                    {"active": False, "status": "corrected"},
                )
        record_memories(
            internal_id,
            {key: value},
            f"User correction: {argument}",
            source_channel="memory_command",
        )
        return f"Corrected `{key}` to `{value}` and retained the provenance trail."
    if action == "confirm":
        target = next(
            (
                item
                for item in memories
                if str(item.get("id") or item.get("_id")) == argument
            ),
            None,
        )
        if not target or target.get("status") != "pending_confirmation":
            return "I could not find that pending memory for this user."
        for old_id in target.get("contradicts", []):
            _update_memory(
                internal_id, str(old_id), {"active": False, "status": "superseded"}
            )
        _update_memory(
            internal_id,
            argument,
            {"active": True, "status": "verified", "last_confirmed_at": _now()},
        )
        _sync_core_profile(internal_id, str(target.get("key", "")), target.get("value"))
        return (
            f"Confirmed `{target.get('key')}` and superseded the contradictory record."
        )
    if action == "rollback":
        key = argument.strip().casefold()
        matching = [
            item for item in memories if str(item.get("key", "")).casefold() == key
        ]
        current = next(
            (item for item in reversed(matching) if item.get("active", True)), None
        )
        previous = next(
            (
                item
                for item in reversed(matching)
                if not item.get("active", True)
                and item.get("status") in {"corrected", "superseded"}
            ),
            None,
        )
        if not current or not previous:
            return f"I could not find a prior value to restore for `{argument}`."
        _update_memory(
            internal_id,
            str(current.get("id") or current.get("_id")),
            {"active": False, "status": "rolled_back"},
        )
        _update_memory(
            internal_id,
            str(previous.get("id") or previous.get("_id")),
            {"active": True, "status": "verified", "last_confirmed_at": _now()},
        )
        _sync_core_profile(
            internal_id, str(previous.get("key", "")), previous.get("value")
        )
        return f"Restored `{previous.get('key')}` to `{previous.get('value')}`."
    if action == "why":
        matching = [
            item
            for item in memories
            if str(item.get("key", "")).casefold() == argument.casefold()
        ]
        if not matching:
            return f"I have no memory named `{argument}`."
        item = matching[-1]
        return (
            f"`{item.get('key')}` = `{item.get('value')}` because of "
            f"{item.get('source')} on {item.get('source_channel', 'unknown')} "
            f"(message `{item.get('source_message_id') or 'unavailable'}`, "
            f"confidence {float(item.get('confidence', 0)):.2f}, status {item.get('status', 'verified')})."
        )
    if not memories:
        return "I have no typed long-term memories for you."
    lines = ["Your typed memories:"]
    for item in memories[:50]:
        lines.append(
            f"- [{item.get('tier') or item.get('kind', 'biography')}] "
            f"{item.get('key')} = {item.get('value')} "
            f"({item.get('status', 'verified')}, source: {item.get('source_channel', 'unknown')})"
        )
    return "\n".join(lines)


def get_matching_abilities(
    internal_id: str, user_text: str, limit: int = 3
) -> list[dict]:
    from memory.learned_skills import matching_skills

    return matching_skills(str(internal_id), user_text, limit)


_PROACTIVE_DISABLE = re.compile(
    r"\b(?:stop|disable|turn off|do not|don't)\b.{0,30}\bproactive\b", re.I
)
_PROACTIVE_TOPIC_REJECTION = re.compile(
    r"(?:no need to (?:keep )?(?:sharing|sending|mentioning)|"
    r"(?:please )?(?:stop|don't|do not) (?:sharing|sending|mentioning|talking about)|"
    r"i(?:'m| am) not interested in)\s+(.{2,80})",
    re.I,
)
_PROACTIVE_FACT_CORRECTION = re.compile(
    r"^(?:no[,!. ]+)?(?:there (?:is|are) no(?: such)?\s+(?P<topic>.{2,80})|"
    r"there(?:'s| is) none)\s*[.!?]*$",
    re.I,
)


def capture_proactive_feedback(internal_id: str, user_text: str) -> dict:
    """Persist explicit proactive opt-out and topic rejection signals."""
    from memory.users import UserManager

    if _PROACTIVE_DISABLE.search(user_text):
        profile = UserManager.get_user_profile(internal_id) or {}
        updates = {
            "proactive_messaging_enabled": False,
            "proactive_rejection_count": min(
                max(0, int(profile.get("proactive_rejection_count", 0))) + 1, 10
            ),
        }
    else:
        match = _PROACTIVE_TOPIC_REJECTION.search(user_text.strip())
        profile = UserManager.get_user_profile(internal_id) or {}
        if match:
            raw_topic = match.group(1)
        else:
            correction = _PROACTIVE_FACT_CORRECTION.search(user_text.strip())
            last_topic = str(profile.get("proactive_last_topic") or "").strip()
            if not (
                correction
                and profile.get("proactive_awaiting_response") is True
                and last_topic
            ):
                return {}
            corrected_topic = str(correction.groupdict().get("topic") or "").casefold()
            corrected_words = re.findall(r"[a-z0-9]{3,}", corrected_topic)
            if corrected_words and not any(
                word in last_topic.casefold() for word in corrected_words
            ):
                return {}
            raw_topic = last_topic
        topic = re.sub(r"[^a-z0-9 +&'-]", "", raw_topic.casefold()).strip()
        topic = re.sub(r"\b(?:please|again|anymore)\b", "", topic).strip()
        if not topic:
            return {}
        existing = [
            str(item).casefold() for item in profile.get("proactive_avoid_topics", [])
        ]
        updates = {
            "proactive_avoid_topics": list(dict.fromkeys([*existing, topic]))[-20:],
            "proactive_rejection_count": min(
                max(0, int(profile.get("proactive_rejection_count", 0))) + 1, 10
            ),
        }
    UserManager.update_user_profile(internal_id, updates)
    try:
        from memory.adaptation import record_explicit_event

        record_explicit_event(str(internal_id), "proactive_rejection", user_text)
    except Exception:
        pass
    return updates


def generate_helpful_prediction(
    internal_id: str, profile: dict, history: list[tuple[str, str]]
) -> Optional[dict]:
    """Use the local neural model to propose—not execute—one grounded action."""
    if not os.getenv("PROACTIVE_PREDICTIONS_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    ):
        return None
    context = "\n".join(f"{role}: {msg[:300]}" for role, msg in history[-10:])
    task_prompt = (
        "Predict one small, concrete way a personal assistant might help next. Base it only "
        "on repeated or explicit evidence below. Return JSON with keys suggestion, reason, "
        "confidence (0 to 1), and evidence_count (number of distinct supporting observations). "
        "Only return a prediction when evidence_count is at least 2. The suggestion must be phrased as a permission-seeking question. "
        "Do not suggest purchases, financial/medical/legal decisions, contacting others, deleting "
        "data, executing commands, or claiming certainty about the user's needs. Return {} when "
        "there is insufficient evidence.\n\n"
        f"Profile: {json.dumps(profile, default=str)[:3000]}\nHistory:\n{context}\n\nJSON only:"
    )
    try:
        from agent.persona_contract import apply_persona_contract
        from llm.manager import ask_llm

        prompt = apply_persona_contract(
            task_prompt,
            medium="proactive recommendation",
            structured_output=True,
        )
        raw = ask_llm(prompt, temperature=0.1, max_tokens=220, role="reasoning")
        match = re.search(r"\{.*?\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        confidence = float(data.get("confidence", 0))
        evidence_count = int(data.get("evidence_count", 0) or 0)
        suggestion = str(data.get("suggestion", "")).strip()
        reason = str(data.get("reason", "")).strip()
        if (
            evidence_count < 2
            or confidence < _PREDICTION_MIN_CONFIDENCE
            or not is_safe_proposed_action(suggestion)
        ):
            return None
        if not suggestion.endswith("?"):
            suggestion += "?"
        result = {
            "suggestion": suggestion[:300],
            "reason": reason[:300],
            "confidence": min(confidence, 1.0),
            "evidence_count": evidence_count,
            "requires_confirmation": True,
            "created_at": _now(),
        }
        if os.getenv("MONGODB_URI"):
            mongo_db.proactive_predictions.insert_one(
                {**result, "internal_id": str(internal_id), "status": "suggested"}
            )
        return result
    except Exception:
        return None
