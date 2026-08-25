"""Guarded long-term learning, declarative abilities, and helpful predictions.

The local LLM supplies neural pattern recognition. This module supplies the
non-neural safety boundary: provenance, confidence thresholds, expiry, consent,
rate limits, and a strict ban on generated-code execution.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import math
from typing import Any, Optional
import uuid

from memory.database import mongo_db

_MEMORY_MIN_CONFIDENCE = float(os.getenv("ADAPTIVE_MEMORY_MIN_CONFIDENCE", "0.8"))
_PREDICTION_MIN_CONFIDENCE = float(
    os.getenv("PROACTIVE_PREDICTION_MIN_CONFIDENCE", "0.82")
)
_INFERRED_TTL_DAYS = int(os.getenv("ADAPTIVE_INFERRED_TTL_DAYS", "45"))
_TEMPORARY_TTL_DAYS = int(os.getenv("ADAPTIVE_TEMPORARY_TTL_DAYS", "14"))

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
    }
)
_SENSITIVE = re.compile(
    r"\b(?:password|passcode|api[_ -]?key|secret|private[_ -]?key|credit[_ -]?card|"
    r"bank[_ -]?account|social security|passport number|medical record|diagnosis)\b",
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


def _memory_enabled(internal_id: str, channel: str | None = None) -> bool:
    from memory.repositories import get_repositories

    profile = get_repositories().profiles.get(str(internal_id))
    return profile.get("memory_enabled", True) and channel not in set(
        profile.get("memory_paused_channels", [])
    )


def _all_owner_memories(internal_id: str) -> list[dict]:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_adaptive_memories

        return list_adaptive_memories(str(internal_id))
    return list(
        mongo_db.adaptive_memories.find(
            {
                "$or": [
                    {"owner_id": str(internal_id)},
                    {"internal_id": str(internal_id)},
                ]
            }
        ).limit(500)
    )


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
    r"^/?memory(?:\s+(?P<action>inspect|timeline|why|forget|export|pause|resume|disable|correct|confirm|rollback))?(?:\s+(?P<argument>.*))?$",
    re.I,
)
_NATURAL_MEMORY = re.compile(
    r"^(?:what do you remember(?: about me)?|forget that|forget everything|"
    r"do not learn from this chat|don't learn from this chat|resume learning from this chat)$",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]
    return clean or hashlib.sha256(text.encode()).hexdigest()[:12]


def is_safe_proposed_action(text: str) -> bool:
    """Predictions may suggest low-risk help, never perform consequential work."""
    return bool(text.strip()) and not _UNSAFE_ACTIONS.search(text)


def record_memories(
    internal_id: str,
    facts: dict[str, Any],
    evidence: str,
    source: str = "explicit_user_statement",
    *,
    source_message_id: str = "",
    source_channel: str = "unknown",
) -> list[dict]:
    """Persist facts with provenance and reinforcement counts."""
    if not internal_id or not facts:
        return []
    if not _memory_enabled(internal_id, source_channel) or _DO_NOT_REMEMBER.search(evidence):
        return []
    now = _now()
    results = []
    for key, value in facts.items():
        if _SENSITIVE.search(f"{key} {value}"):
            continue
        kind = _memory_kind(str(key), source)
        existing = [
            item
            for item in _all_owner_memories(internal_id)
            if item.get("active", True)
            and str(item.get("key", "")).casefold() == str(key).casefold()
        ]
        same = next((item for item in existing if item.get("value") == value), None)
        contradictory = [item for item in existing if item.get("value") != value]
        memory_id = (
            str(same.get("id") or same.get("_id")) if same else str(uuid.uuid4())
        )
        status = (
            "pending_confirmation"
            if contradictory
            else "verified" if source == "explicit_user_statement" else "hypothesis"
        )
        document = {
            "_id": memory_id,
            "id": memory_id,
            "owner_id": str(internal_id),
            "internal_id": str(internal_id),  # migration compatibility
            "kind": kind,
            "key": str(key)[:64],
            "value": value,
            "source": source,
            "source_message_id": str(source_message_id)[:128],
            "source_channel": str(source_channel)[:64],
            "confidence": (
                1.0 if source == "explicit_user_statement" else _MEMORY_MIN_CONFIDENCE
            ),
            "evidence": evidence[:500],
            "contradicts": [
                str(item.get("id") or item.get("_id")) for item in contradictory
            ],
            "status": status,
            "sensitivity": "ordinary",
            "created_at": same.get("created_at", now) if same else now,
            "last_confirmed_at": now if source == "explicit_user_statement" else None,
            "last_seen_at": now,
            "expires_at": (
                now + timedelta(days=_TEMPORARY_TTL_DAYS)
                if kind == "temporary_context"
                else (
                    None
                    if source == "explicit_user_statement"
                    else now + timedelta(days=_INFERRED_TTL_DAYS)
                )
            ),
            "active": not contradictory,
        }
        if not os.getenv("MONGODB_URI"):
            from memory.local_store import upsert_adaptive_memory

            upsert_adaptive_memory(document)
            results.append(document)
            continue
        mongo_db.adaptive_memories.update_one(
            {"_id": memory_id},
            {
                "$set": {
                    **{
                        k: v
                        for k, v in document.items()
                        if k not in {"_id", "created_at"}
                    },
                },
                "$inc": {"confirmation_count": 1},
                "$setOnInsert": {"created_at": document["created_at"]},
            },
            upsert=True,
        )
        results.append(document)
    return results


class LocalEmbeddingModel:
    """Small dependency-free feature-hashing embedding model for private retrieval."""

    dimensions = 128

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[a-z0-9]+", text.casefold())
        features = tokens + [
            token[i : i + 3] for token in tokens for i in range(max(0, len(token) - 2))
        ]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=4).digest()
            vector[int.from_bytes(digest, "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


_EMBEDDER = LocalEmbeddingModel()


def get_relevant_memories(internal_id: str, query: str, limit: int = 8) -> list[dict]:
    """Owner-filter first, then semantically rank active non-expired memories."""
    if not _memory_enabled(internal_id):
        return []
    now = _now()
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import list_adaptive_memories

        docs = list_adaptive_memories(str(internal_id))
        active_docs = []
        for doc in docs:
            if not doc.get("active", True):
                continue
            expires_at = doc.get("expires_at")
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
            if isinstance(expires_at, datetime):
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= now:
                    continue
            active_docs.append(doc)
        docs = active_docs
    else:
        docs = list(
            mongo_db.adaptive_memories.find(
                {
                    "$and": [
                        {
                            "$or": [
                                {"owner_id": str(internal_id)},
                                {"internal_id": str(internal_id)},
                            ]
                        },
                        {"$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}]},
                    ],
                    "active": True,
                }
            ).limit(100)
        )
    query_vector = _EMBEDDER.encode(query)
    for doc in docs:
        text = f"{doc.get('key', '')} {doc.get('value', '')}".lower()
        vector = _EMBEDDER.encode(text)
        similarity = sum(left * right for left, right in zip(query_vector, vector))
        doc["_relevance"] = similarity + 0.1 * float(doc.get("confidence", 0))
    docs.sort(
        key=lambda d: (d["_relevance"], d.get("confirmation_count", 0)), reverse=True
    )
    return docs[:limit]


def get_pending_memory_conflicts(internal_id: str) -> list[dict]:
    """Return only this owner's unresolved contradictions for confirmation UI."""
    return [
        item
        for item in _all_owner_memories(str(internal_id))
        if item.get("status") == "pending_confirmation"
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
    natural = _NATURAL_MEMORY.fullmatch(text.strip())
    if natural:
        from memory.repositories import get_repositories

        lowered = text.strip().casefold()
        if lowered.startswith("what do you remember"):
            return _handle_memory_command(str(internal_id), "inspect", "")
        if lowered in {"forget that", "forget everything"}:
            return _handle_memory_command(str(internal_id), "forget", "all")
        profile = get_repositories().profiles.get(str(internal_id))
        paused = set(profile.get("memory_paused_channels", []))
        if lowered.startswith(("do not", "don't")) and channel:
            paused.add(channel)
            get_repositories().profiles.update(str(internal_id), {"memory_paused_channels": sorted(paused)})
            return f"I won’t learn from this {channel} chat. Existing memories are unchanged."
        if channel:
            paused.discard(channel)
            get_repositories().profiles.update(str(internal_id), {"memory_paused_channels": sorted(paused)})
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
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import delete_adaptive_memories

        return delete_adaptive_memories(internal_id, key)
    query: dict[str, Any] = {
        "$or": [{"owner_id": internal_id}, {"internal_id": internal_id}]
    }
    if key is not None:
        query["key"] = {"$regex": f"^{re.escape(key)}$", "$options": "i"}
    return int(
        getattr(mongo_db.adaptive_memories.delete_many(query), "deleted_count", 0)
    )


def _update_memory(internal_id: str, memory_id: str, updates: dict) -> bool:
    if not os.getenv("MONGODB_URI"):
        from memory.local_store import update_adaptive_memory

        return update_adaptive_memory(memory_id, internal_id, updates)
    result = mongo_db.adaptive_memories.update_one(
        {"_id": memory_id, "owner_id": internal_id}, {"$set": updates}
    )
    return bool(getattr(result, "modified_count", 0))


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
    if action == "timeline":
        ordered = sorted(memories, key=lambda item: str(item.get("created_at", "")), reverse=True)
        if not ordered:
            return "Your memory timeline is empty."
        return "Your memory timeline:\n" + "\n".join(
            f"- {item.get('created_at')}: {item.get('key')} = {item.get('value')} "
            f"[{item.get('status', 'verified')}]" for item in ordered[:50]
        )
    if action == "forget":
        key = None if argument.casefold() in {"", "all", "everything"} else argument
        count = _forget_memories(internal_id, key)
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
        return (
            f"Confirmed `{target.get('key')}` and superseded the contradictory record."
        )
    if action == "rollback":
        key = argument.strip().casefold()
        matching = [item for item in memories if str(item.get("key", "")).casefold() == key]
        current = next((item for item in reversed(matching) if item.get("active", True)), None)
        previous = next(
            (item for item in reversed(matching) if not item.get("active", True) and item.get("status") in {"corrected", "superseded"}),
            None,
        )
        if not current or not previous:
            return f"I could not find a prior value to restore for `{argument}`."
        _update_memory(internal_id, str(current.get("id") or current.get("_id")), {"active": False, "status": "rolled_back"})
        _update_memory(internal_id, str(previous.get("id") or previous.get("_id")), {"active": True, "status": "verified", "last_confirmed_at": _now()})
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
            f"- [{item.get('kind', 'biography')}] {item.get('key')} = {item.get('value')} "
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
        if not match:
            return {}
        topic = re.sub(r"[^a-z0-9 +&'-]", "", match.group(1).casefold()).strip()
        topic = re.sub(r"\b(?:please|again|anymore)\b", "", topic).strip()
        if not topic:
            return {}
        profile = UserManager.get_user_profile(internal_id) or {}
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
    prompt = (
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
        from llm.manager import ask_llm

        raw = ask_llm(prompt, temperature=0.1, max_tokens=220, role="reasoning")
        match = re.search(r"\{.*?\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        confidence = float(data.get("confidence", 0))
        evidence_count = int(data.get("evidence_count", 0) or 0)
        suggestion = str(data.get("suggestion", "")).strip()
        reason = str(data.get("reason", "")).strip()
        if evidence_count < 2 or confidence < _PREDICTION_MIN_CONFIDENCE or not is_safe_proposed_action(
            suggestion
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
