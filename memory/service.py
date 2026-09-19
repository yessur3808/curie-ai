"""Unified owner-scoped memory service for Curie.

Repositories in this module only persist records.  Memory policy, relevance,
contradictions, retention, provenance, and consolidation belong to
``MemoryService`` so SQLite and Mongo deployments behave the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import os
import re
import threading
from typing import Any, Iterable, Mapping, Protocol
import uuid

from memory.hierarchy import default_importance, rank_memories


class MemoryTier(str, Enum):
    WORKING = "working_context"
    CORE = "core"
    EPISODIC = "episodic"
    ARCHIVAL = "archival"
    PROCEDURAL = "procedural_adaptation"
    OPERATIONAL = "operational"


class ConfirmationState(str, Enum):
    CONFIRMED = "confirmed"
    RECORDED = "recorded"
    CANDIDATE = "candidate"
    CONFLICTED = "conflicted"
    INVALIDATED = "invalidated"


class WriteDecision(str, Enum):
    AUTO = "auto"
    CANDIDATE = "candidate"
    REJECT = "reject"


_TYPE_TIER = {
    "identity": MemoryTier.CORE,
    "preference": MemoryTier.CORE,
    "assistant_setting": MemoryTier.CORE,
    "routine": MemoryTier.CORE,
    "temporary_context": MemoryTier.EPISODIC,
    "episode": MemoryTier.EPISODIC,
    "project": MemoryTier.ARCHIVAL,
    "relationship": MemoryTier.ARCHIVAL,
    "biography": MemoryTier.ARCHIVAL,
    "hypothesis": MemoryTier.ARCHIVAL,
    "adaptation": MemoryTier.PROCEDURAL,
    "feedback": MemoryTier.PROCEDURAL,
    "active_task": MemoryTier.OPERATIONAL,
    "device_alias": MemoryTier.OPERATIONAL,
}
_CANDIDATE_TYPES = frozenset(
    {"routine", "project", "relationship", "repeated_topic", "schedule", "device_alias"}
)
_SECRET = re.compile(
    r"\b(?:password|passcode|pin|otp|one.time code|api[_ -]?key|secret|"
    r"private[_ -]?key|seed phrase|recovery phrase|access[_ -]?token|"
    r"credit[_ -]?card|bank[_ -]?account|wallet key|financial credential)\b|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----|"
    r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b",
    re.I,
)
_DO_NOT_REMEMBER = re.compile(
    r"\b(?:do not|don't|dont|never)\s+(?:save|store|remember|memorize)\b|"
    r"\boff the record\b",
    re.I,
)
_TRANSIENT_EMOTION = re.compile(
    r"\b(?:mood|emotion|feeling|feels|sad|happy|angry|anxious|lonely|upset)\b",
    re.I,
)
_OPERATIONAL_COMMAND = re.compile(
    r"^(?:(?:please\s+)|(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?))?"
    r"(?:turn|switch|set|start|stop|open|close|lock|unlock|enable|disable|run|"
    r"send|cancel|pause|resume|schedule)\b",
    re.I,
)
_PROFILE_RUNTIME_KEYS = frozenset(
    {
        "timezone",
        "location",
        "last_user_interaction_at",
        "last_proactive_at",
        "last_proactive_generation_at",
        "proactive_count_date",
        "proactive_count_today",
        "memory_enabled",
        "memory_paused_channels",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _normal(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _same_value(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(
        right, sort_keys=True, default=str
    )


def _legacy_status(state: ConfirmationState) -> str:
    return {
        ConfirmationState.CONFIRMED: "verified",
        ConfirmationState.RECORDED: "recorded",
        ConfirmationState.CANDIDATE: "hypothesis",
        ConfirmationState.CONFLICTED: "pending_confirmation",
        ConfirmationState.INVALIDATED: "superseded",
    }[state]


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    record_id: str
    owner_id: str
    tier: MemoryTier
    type: str
    canonical_subject: str
    predicate: str
    value: Any
    source: str
    source_turn: str
    created_at: datetime
    updated_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    confidence: float
    confirmation_state: ConfirmationState
    importance: float
    sensitivity: str
    retention_class: str
    contradiction_group: str | None = None
    last_retrieved_at: datetime | None = None
    retrieval_count: int = 0
    tombstone: bool = False
    source_channel: str = "unknown"
    evidence: str = ""
    confirmation_count: int = 1
    contradicted_record_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id or not self.owner_id:
            raise ValueError("memory records require record and owner identifiers")
        if not self.predicate.strip():
            raise ValueError("memory records require a predicate")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("memory confidence must be between zero and one")
        if not 0.0 <= float(self.importance) <= 1.0:
            raise ValueError("memory importance must be between zero and one")

    @property
    def active(self) -> bool:
        return not self.tombstone and self.confirmation_state not in {
            ConfirmationState.CONFLICTED,
            ConfirmationState.INVALIDATED,
        }

    def to_document(self) -> dict[str, Any]:
        document = {
            "schema_version": 1,
            "record_id": self.record_id,
            "id": self.record_id,
            "_id": self.record_id,
            "owner_id": self.owner_id,
            "internal_id": self.owner_id,
            "tier": self.tier.value,
            "type": self.type,
            "kind": self.type,
            "canonical_subject": self.canonical_subject,
            "predicate": self.predicate,
            "key": self.predicate,
            "value": self.value,
            "source": self.source,
            "source_turn": self.source_turn,
            "source_message_id": self.source_turn,
            "source_channel": self.source_channel,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "valid_from": _iso(self.valid_from),
            "valid_until": _iso(self.valid_until),
            "expires_at": _iso(self.valid_until),
            "confidence": self.confidence,
            "confirmation_state": self.confirmation_state.value,
            "status": _legacy_status(self.confirmation_state),
            "importance": self.importance,
            "sensitivity": self.sensitivity,
            "retention_class": self.retention_class,
            "contradiction_group": self.contradiction_group,
            "contradicts": list(self.contradicted_record_ids),
            "last_retrieved_at": _iso(self.last_retrieved_at),
            "retrieval_count": self.retrieval_count,
            "tombstone": self.tombstone,
            "active": self.active,
            "evidence": self.evidence,
            "confirmation_count": self.confirmation_count,
        }
        document.update(dict(self.metadata))
        return document

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "MemoryRecord":
        now = _now()
        kind = str(document.get("type") or document.get("kind") or "biography")
        tier_value = str(document.get("tier") or "")
        try:
            tier = MemoryTier(tier_value)
        except ValueError:
            tier = _TYPE_TIER.get(kind, MemoryTier.ARCHIVAL)
        status = str(
            document.get("confirmation_state") or document.get("status") or "verified"
        ).casefold()
        state = {
            "confirmed": ConfirmationState.CONFIRMED,
            "verified": ConfirmationState.CONFIRMED,
            "recorded": ConfirmationState.RECORDED,
            "candidate": ConfirmationState.CANDIDATE,
            "hypothesis": ConfirmationState.CANDIDATE,
            "conflicted": ConfirmationState.CONFLICTED,
            "pending_confirmation": ConfirmationState.CONFLICTED,
            "invalidated": ConfirmationState.INVALIDATED,
            "corrected": ConfirmationState.INVALIDATED,
            "superseded": ConfirmationState.INVALIDATED,
            "rolled_back": ConfirmationState.INVALIDATED,
        }.get(status, ConfirmationState.CONFIRMED)
        created = _as_datetime(document.get("created_at")) or now
        updated = _as_datetime(document.get("updated_at")) or created
        record_id = str(
            document.get("record_id")
            or document.get("id")
            or document.get("_id")
            or uuid.uuid4()
        )
        known = {
            "schema_version",
            "record_id",
            "id",
            "_id",
            "owner_id",
            "internal_id",
            "tier",
            "type",
            "kind",
            "canonical_subject",
            "predicate",
            "key",
            "value",
            "source",
            "source_turn",
            "source_message_id",
            "source_channel",
            "created_at",
            "updated_at",
            "valid_from",
            "valid_until",
            "expires_at",
            "confidence",
            "confirmation_state",
            "status",
            "importance",
            "sensitivity",
            "retention_class",
            "contradiction_group",
            "contradicts",
            "last_retrieved_at",
            "retrieval_count",
            "tombstone",
            "active",
            "evidence",
            "confirmation_count",
        }
        return cls(
            record_id=record_id,
            owner_id=str(document.get("owner_id") or document.get("internal_id") or ""),
            tier=tier,
            type=kind,
            canonical_subject=str(document.get("canonical_subject") or "user"),
            predicate=str(document.get("predicate") or document.get("key") or kind),
            value=document.get("value"),
            source=str(document.get("source") or "legacy_import"),
            source_turn=str(
                document.get("source_turn") or document.get("source_message_id") or ""
            ),
            created_at=created,
            updated_at=updated,
            valid_from=_as_datetime(document.get("valid_from")) or created,
            valid_until=_as_datetime(
                document.get("valid_until") or document.get("expires_at")
            ),
            confidence=max(0.0, min(float(document.get("confidence", 1.0)), 1.0)),
            confirmation_state=state,
            importance=max(
                0.0,
                min(float(document.get("importance", default_importance(kind))), 1.0),
            ),
            sensitivity=str(document.get("sensitivity") or "ordinary"),
            retention_class=str(document.get("retention_class") or "standard"),
            contradiction_group=(
                str(document["contradiction_group"])
                if document.get("contradiction_group")
                else None
            ),
            last_retrieved_at=_as_datetime(document.get("last_retrieved_at")),
            retrieval_count=max(0, int(document.get("retrieval_count", 0) or 0)),
            tombstone=bool(document.get("tombstone", False)),
            source_channel=str(document.get("source_channel") or "unknown"),
            evidence=str(document.get("evidence") or "")[:500],
            confirmation_count=max(1, int(document.get("confirmation_count", 1) or 1)),
            contradicted_record_ids=tuple(
                str(item) for item in document.get("contradicts", ())
            ),
            metadata={
                key: value for key, value in document.items() if key not in known
            },
        )


class MemoryRepository(Protocol):
    def upsert(self, record: MemoryRecord) -> None: ...

    def get(self, owner_id: str, record_id: str) -> MemoryRecord | None: ...

    def list_owner(self, owner_id: str) -> list[MemoryRecord]: ...

    def record_retrieval(
        self, owner_id: str, query_hash: str, record_ids: Iterable[str], outcome: str
    ) -> None: ...

    def legacy_documents(self, owner_id: str) -> list[dict[str, Any]]: ...

    def save_migration_backup(
        self,
        run_id: str,
        owner_id: str,
        documents: Iterable[Mapping[str, Any]],
        expires_at: datetime,
    ) -> None: ...

    def set_cutover_mode(self, owner_id: str, mode: str) -> None: ...

    def cutover_mode(self, owner_id: str) -> str: ...

    def compact(self, owner_id: str) -> None: ...


class SQLiteMemoryRepository:
    """SQLite storage adapter with compatibility shadow reads and dual writes."""

    @staticmethod
    def _local_store():
        from memory import local_store

        return local_store

    def upsert(self, record: MemoryRecord) -> None:
        store = self._local_store()
        document = record.to_document()
        with store._LOCK, store._managed_connection() as connection:
            connection.execute(
                "INSERT INTO memory_records(record_id,owner_id,tier,type,subject,predicate,"
                "document_json,tombstone,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(owner_id,record_id) DO UPDATE SET tier=excluded.tier,"
                "type=excluded.type,subject=excluded.subject,predicate=excluded.predicate,"
                "document_json=excluded.document_json,tombstone=excluded.tombstone,"
                "updated_at=excluded.updated_at",
                (
                    record.record_id,
                    record.owner_id,
                    record.tier.value,
                    record.type,
                    record.canonical_subject,
                    record.predicate,
                    json.dumps(document, default=str),
                    int(record.tombstone),
                    _iso(record.updated_at),
                ),
            )
        # Existing memory controls still read adaptive_memories.  A temporary
        # compatibility dual write keeps those controls correct during cutover.
        store.upsert_adaptive_memory(document)

    def legacy_documents(self, owner_id: str) -> list[dict[str, Any]]:
        return self._local_store().list_adaptive_memories(str(owner_id))

    def list_owner(self, owner_id: str) -> list[MemoryRecord]:
        store = self._local_store()
        with store._LOCK, store._managed_connection() as connection:
            rows = connection.execute(
                "SELECT document_json FROM memory_records WHERE owner_id=?",
                (str(owner_id),),
            ).fetchall()
        documents = [json.loads(row["document_json"]) for row in rows]
        if self.cutover_mode(str(owner_id)) != "unified":
            documents.extend(self.legacy_documents(str(owner_id)))
        newest: dict[str, MemoryRecord] = {}
        for document in documents:
            record = MemoryRecord.from_document(document)
            if record.owner_id != str(owner_id):
                continue
            previous = newest.get(record.record_id)
            if previous is None or record.updated_at >= previous.updated_at:
                newest[record.record_id] = record
        return list(newest.values())

    def get(self, owner_id: str, record_id: str) -> MemoryRecord | None:
        return next(
            (item for item in self.list_owner(owner_id) if item.record_id == record_id),
            None,
        )

    def record_retrieval(
        self, owner_id: str, query_hash: str, record_ids: Iterable[str], outcome: str
    ) -> None:
        store = self._local_store()
        with store._LOCK, store._managed_connection() as connection:
            connection.execute(
                "INSERT INTO memory_retrieval_events(owner_id,query_hash,record_ids_json,"
                "outcome,created_at) VALUES(?,?,?,?,?)",
                (
                    str(owner_id),
                    str(query_hash),
                    json.dumps(list(record_ids)),
                    str(outcome),
                    _iso(_now()),
                ),
            )

    def save_migration_backup(
        self,
        run_id: str,
        owner_id: str,
        documents: Iterable[Mapping[str, Any]],
        expires_at: datetime,
    ) -> None:
        store = self._local_store()
        with store._LOCK, store._managed_connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO memory_migration_backups"
                "(run_id,owner_id,backup_json,created_at,expires_at) VALUES(?,?,?,?,?)",
                (
                    str(run_id),
                    str(owner_id),
                    json.dumps(list(documents), default=str),
                    _iso(_now()),
                    _iso(expires_at),
                ),
            )

    def set_cutover_mode(self, owner_id: str, mode: str) -> None:
        if mode not in {"shadow", "unified", "legacy"}:
            raise ValueError("invalid memory cutover mode")
        store = self._local_store()
        with store._LOCK, store._managed_connection() as connection:
            connection.execute(
                "INSERT INTO memory_owner_cutovers(owner_id,mode,updated_at) "
                "VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET "
                "mode=excluded.mode,updated_at=excluded.updated_at",
                (str(owner_id), mode, _iso(_now())),
            )

    def cutover_mode(self, owner_id: str) -> str:
        store = self._local_store()
        with store._LOCK, store._managed_connection() as connection:
            row = connection.execute(
                "SELECT mode FROM memory_owner_cutovers WHERE owner_id=?",
                (str(owner_id),),
            ).fetchone()
        return str(row["mode"]) if row else "shadow"

    def compact(self, owner_id: str) -> None:
        store = self._local_store()
        with store._LOCK, store._managed_connection() as connection:
            connection.execute("REINDEX idx_memory_records_owner_tier")
            connection.execute("REINDEX idx_memory_records_owner_predicate")


class MongoMemoryRepository:
    """Mongo storage adapter with the same policy-neutral repository contract."""

    def __init__(self, database: Any | None = None):
        self._database_override = database

    def _database(self):
        if self._database_override is not None:
            return self._database_override
        from memory.database import mongo_db

        return mongo_db

    def upsert(self, record: MemoryRecord) -> None:
        document = record.to_document()
        database = self._database()
        database.unified_memories.replace_one(
            {"record_id": record.record_id, "owner_id": record.owner_id},
            document,
            upsert=True,
        )
        database.adaptive_memories.update_one(
            {"_id": record.record_id},
            {
                "$set": {
                    key: value
                    for key, value in document.items()
                    if key not in {"_id", "created_at", "confirmation_count"}
                },
                "$inc": {"confirmation_count": 1},
                "$setOnInsert": {"created_at": document["created_at"]},
            },
            upsert=True,
        )

    def legacy_documents(self, owner_id: str) -> list[dict[str, Any]]:
        return list(
            self._database()
            .adaptive_memories.find(
                {
                    "$or": [
                        {"owner_id": str(owner_id)},
                        {"internal_id": str(owner_id)},
                    ]
                }
            )
            .limit(2000)
        )

    def list_owner(self, owner_id: str) -> list[MemoryRecord]:
        database = self._database()
        documents = list(
            database.unified_memories.find({"owner_id": str(owner_id)}).limit(2000)
        )
        if self.cutover_mode(str(owner_id)) != "unified":
            documents.extend(self.legacy_documents(owner_id))
        newest: dict[str, MemoryRecord] = {}
        for document in documents:
            record = MemoryRecord.from_document(document)
            if record.owner_id != str(owner_id):
                continue
            previous = newest.get(record.record_id)
            if previous is None or record.updated_at >= previous.updated_at:
                newest[record.record_id] = record
        return list(newest.values())

    def get(self, owner_id: str, record_id: str) -> MemoryRecord | None:
        return next(
            (item for item in self.list_owner(owner_id) if item.record_id == record_id),
            None,
        )

    def record_retrieval(
        self, owner_id: str, query_hash: str, record_ids: Iterable[str], outcome: str
    ) -> None:
        self._database().memory_retrieval_events.insert_one(
            {
                "owner_id": str(owner_id),
                "query_hash": str(query_hash),
                "record_ids": list(record_ids),
                "outcome": str(outcome),
                "created_at": _now(),
            }
        )

    def save_migration_backup(
        self,
        run_id: str,
        owner_id: str,
        documents: Iterable[Mapping[str, Any]],
        expires_at: datetime,
    ) -> None:
        self._database().memory_migration_backups.replace_one(
            {"run_id": str(run_id), "owner_id": str(owner_id)},
            {
                "run_id": str(run_id),
                "owner_id": str(owner_id),
                "documents": list(documents),
                "created_at": _now(),
                "expires_at": expires_at,
            },
            upsert=True,
        )

    def set_cutover_mode(self, owner_id: str, mode: str) -> None:
        if mode not in {"shadow", "unified", "legacy"}:
            raise ValueError("invalid memory cutover mode")
        self._database().memory_owner_cutovers.update_one(
            {"owner_id": str(owner_id)},
            {"$set": {"mode": mode, "updated_at": _now()}},
            upsert=True,
        )

    def cutover_mode(self, owner_id: str) -> str:
        item = self._database().memory_owner_cutovers.find_one(
            {"owner_id": str(owner_id)}
        )
        return str((item or {}).get("mode") or "shadow")

    def compact(self, owner_id: str) -> None:
        collection = self._database().unified_memories
        collection.create_index([("owner_id", 1), ("tier", 1), ("tombstone", 1)])
        collection.create_index([("owner_id", 1), ("predicate", 1)])


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    record: MemoryRecord
    score: float
    reason: str

    def as_document(self) -> dict[str, Any]:
        document = self.record.to_document()
        document.update(
            {
                "_relevance": self.score,
                "_retrieval_reason": self.reason,
                "_memory_tier": self.record.tier.value,
                "_provenance": {
                    "record_id": self.record.record_id,
                    "source": self.record.source,
                    "source_turn": self.record.source_turn,
                    "confirmation_state": self.record.confirmation_state.value,
                },
            }
        )
        return document


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    owner_id: str
    outcome: str
    hits: tuple[RetrievalHit, ...]
    query_hash: str
    excluded_count: int = 0

    def as_documents(self) -> list[dict[str, Any]]:
        return [item.as_document() for item in self.hits]


@dataclass(frozen=True, slots=True)
class ConsolidationAction:
    action: str
    record_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ConsolidationPlan:
    owner_id: str
    dry_run: bool
    actions: tuple[ConsolidationAction, ...]


@dataclass(frozen=True, slots=True)
class MigrationReport:
    run_id: str
    owner_id: str
    dry_run: bool
    source_count: int
    migrated_count: int
    target_count: int
    missing_record_ids: tuple[str, ...]
    ownership_mismatches: tuple[str, ...]
    backup_documents: tuple[Mapping[str, Any], ...]
    rollback_until: datetime

    @property
    def valid(self) -> bool:
        return not self.missing_record_ids and not self.ownership_mismatches


class MemoryService:
    """One policy and retrieval boundary for all long-term agent memory."""

    def __init__(self, repository: MemoryRepository):
        self.repository = repository

    @staticmethod
    def write_decision(
        *, type: str, source: str, predicate: str, value: Any, evidence: str
    ) -> WriteDecision:
        material = f"{predicate} {value} {evidence}"
        if _SECRET.search(material) or _DO_NOT_REMEMBER.search(evidence):
            return WriteDecision.REJECT
        normalized_source = source.casefold()
        if normalized_source in {
            "unsupported_model_guess",
            "unverified_model_guess",
            "third_party_inference",
        }:
            return WriteDecision.REJECT
        if normalized_source in {"inference", "model_inference"} and (
            type in {"identity", "biography"}
            and _TRANSIENT_EMOTION.search(f"{predicate} {value}")
        ):
            return WriteDecision.REJECT
        if normalized_source in {
            "explicit_user_statement",
            "explicit_conversation_episode",
            "explicit_correction",
            "user_feedback",
        }:
            return WriteDecision.AUTO
        if type in _CANDIDATE_TYPES or normalized_source in {
            "inference",
            "model_inference",
            "repeated_observation",
        }:
            return WriteDecision.CANDIDATE
        if type == "active_task":
            return WriteDecision.AUTO
        return WriteDecision.CANDIDATE

    def remember(
        self,
        owner_id: str,
        *,
        predicate: str,
        value: Any,
        type: str = "biography",
        canonical_subject: str = "user",
        source: str = "explicit_user_statement",
        source_turn: str = "",
        source_channel: str = "unknown",
        evidence: str = "",
        tier: MemoryTier | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        sensitivity: str = "ordinary",
        retention_class: str = "standard",
        valid_until: datetime | None = None,
        task_relevant: bool = False,
    ) -> MemoryRecord | None:
        owner_id = str(owner_id).strip()
        predicate = str(predicate).strip()[:128]
        if not owner_id or not predicate:
            return None
        if (
            str(canonical_subject).casefold().startswith("third_party:")
            and not task_relevant
        ):
            return None
        decision = self.write_decision(
            type=type,
            source=source,
            predicate=predicate,
            value=value,
            evidence=evidence,
        )
        if decision is WriteDecision.REJECT:
            return None
        now = _now()
        chosen_tier = tier or _TYPE_TIER.get(type, MemoryTier.ARCHIVAL)
        state = (
            ConfirmationState.CONFIRMED
            if decision is WriteDecision.AUTO
            else ConfirmationState.CANDIDATE
        )
        if type == "episode":
            state = ConfirmationState.RECORDED
        existing = [
            item
            for item in self.repository.list_owner(owner_id)
            if not item.tombstone
            and _normal(item.canonical_subject) == _normal(canonical_subject)
            and _normal(item.predicate) == _normal(predicate)
            and item.confirmation_state is not ConfirmationState.INVALIDATED
        ]
        same = next((item for item in existing if _same_value(item.value, value)), None)
        if same:
            reinforced = replace(
                same,
                updated_at=now,
                confidence=max(same.confidence, float(confidence or same.confidence)),
                confirmation_count=same.confirmation_count + 1,
                evidence=str(evidence or same.evidence)[:500],
                source_turn=str(source_turn or same.source_turn),
                source_channel=str(source_channel or same.source_channel),
            )
            self.repository.upsert(reinforced)
            return reinforced

        contradictory = [item for item in existing if item.active]
        contradiction_group = None
        contradicted_ids: tuple[str, ...] = ()
        record_id = uuid.uuid4().hex
        if contradictory:
            contradiction_group = hashlib.sha256(
                f"{owner_id}:{canonical_subject}:{predicate}".encode()
            ).hexdigest()[:24]
            contradicted_ids = tuple(item.record_id for item in contradictory)
            if source == "explicit_correction":
                for old in contradictory:
                    self.repository.upsert(
                        replace(
                            old,
                            updated_at=now,
                            confirmation_state=ConfirmationState.INVALIDATED,
                            tombstone=True,
                            contradiction_group=contradiction_group,
                        )
                    )
                state = ConfirmationState.CONFIRMED
            elif decision is WriteDecision.CANDIDATE and all(
                item.confirmation_state is ConfirmationState.CANDIDATE
                for item in contradictory
            ):
                for old in contradictory:
                    self.repository.upsert(
                        replace(
                            old,
                            updated_at=now,
                            confirmation_state=ConfirmationState.CONFLICTED,
                            contradiction_group=contradiction_group,
                            contradicted_record_ids=tuple(
                                dict.fromkeys((*old.contradicted_record_ids, record_id))
                            ),
                        )
                    )
                state = ConfirmationState.CONFLICTED
            else:
                state = ConfirmationState.CONFLICTED

        inferred_expiry = valid_until
        if inferred_expiry is None and decision is WriteDecision.CANDIDATE:
            inferred_expiry = now + timedelta(
                days=max(1, int(os.getenv("ADAPTIVE_INFERRED_TTL_DAYS", "45")))
            )
        if inferred_expiry is None and type == "temporary_context":
            inferred_expiry = now + timedelta(
                days=max(1, int(os.getenv("ADAPTIVE_TEMPORARY_TTL_DAYS", "14")))
            )
        record = MemoryRecord(
            record_id=record_id,
            owner_id=owner_id,
            tier=chosen_tier,
            type=str(type),
            canonical_subject=str(canonical_subject or "user"),
            predicate=predicate,
            value=value,
            source=str(source),
            source_turn=str(source_turn)[:128],
            created_at=now,
            updated_at=now,
            valid_from=now,
            valid_until=inferred_expiry,
            confidence=max(
                0.0,
                min(
                    float(
                        confidence
                        if confidence is not None
                        else (1.0 if decision is WriteDecision.AUTO else 0.8)
                    ),
                    1.0,
                ),
            ),
            confirmation_state=state,
            importance=max(
                0.0,
                min(
                    float(
                        importance
                        if importance is not None
                        else default_importance(type)
                    ),
                    1.0,
                ),
            ),
            sensitivity=str(sensitivity),
            retention_class=str(retention_class),
            contradiction_group=contradiction_group,
            source_channel=str(source_channel)[:64],
            evidence=str(evidence)[:500],
            contradicted_record_ids=contradicted_ids,
        )
        self.repository.upsert(record)
        return record

    def retrieve(
        self,
        owner_id: str,
        query: str,
        *,
        limit: int = 8,
        char_budget: int | None = None,
        explicit_search: bool = False,
        tiers: Iterable[MemoryTier] | None = None,
        supplemental_records: Iterable[MemoryRecord] = (),
        consequential: bool = False,
    ) -> RetrievalResult:
        owner_id = str(owner_id)
        query = str(query or "").strip()
        query_hash = hashlib.sha256(query.casefold().encode()).hexdigest()
        if not query:
            result = RetrievalResult(owner_id, "empty_query", (), query_hash)
            self.repository.record_retrieval(owner_id, query_hash, (), result.outcome)
            return result
        if _OPERATIONAL_COMMAND.search(query) and not explicit_search:
            result = RetrievalResult(owner_id, "operational_bypass", (), query_hash)
            self.repository.record_retrieval(owner_id, query_hash, (), result.outcome)
            return result
        allowed = set(
            tiers
            or {
                MemoryTier.WORKING,
                MemoryTier.CORE,
                MemoryTier.EPISODIC,
                MemoryTier.ARCHIVAL,
                MemoryTier.PROCEDURAL,
            }
        )
        # Operational state is intentionally opt-in and never becomes a
        # personal fact merely because a query shares a device/task keyword.
        if tiers is None:
            allowed.discard(MemoryTier.OPERATIONAL)
        now = _now()
        combined = [*self.repository.list_owner(owner_id), *supplemental_records]
        all_records = list(
            {
                item.record_id: item for item in combined if item.owner_id == owner_id
            }.values()
        )
        eligible: list[MemoryRecord] = []
        stale_confirmed: list[str] = []
        stale_days = max(
            1, int(os.getenv("MEMORY_CONSEQUENTIAL_RECONFIRM_DAYS", "180"))
        )
        for record in all_records:
            if record.owner_id != owner_id or record.tier not in allowed:
                continue
            if record.tombstone or not record.active:
                continue
            if record.valid_until and record.valid_until <= now:
                continue
            if (
                consequential
                and record.confirmation_state is ConfirmationState.CONFIRMED
                and now - record.updated_at > timedelta(days=stale_days)
            ):
                stale_confirmed.append(record.record_id)
                continue
            if (
                record.confirmation_state is ConfirmationState.CANDIDATE
                and record.confidence < 0.8
            ):
                continue
            eligible.append(record)
        ranked = rank_memories(
            query,
            [record.to_document() for record in eligible],
            limit=max(1, int(limit)),
            char_budget=char_budget,
            explicit_search=explicit_search,
        )
        by_id = {record.record_id: record for record in eligible}
        hits: list[RetrievalHit] = []
        for document in ranked:
            record_id = str(document.get("record_id") or document.get("id") or "")
            record = by_id.get(record_id)
            if record is None:
                continue
            retrieved = replace(
                record,
                last_retrieved_at=now,
                retrieval_count=record.retrieval_count + 1,
            )
            self.repository.upsert(retrieved)
            hits.append(
                RetrievalHit(
                    retrieved,
                    float(document.get("_relevance", 0.0)),
                    str(document.get("_retrieval_reason") or "ranked relevance"),
                )
            )
        outcome = (
            "selected"
            if hits
            else "reconfirmation_required" if stale_confirmed else "no_relevant_memory"
        )
        self.repository.record_retrieval(
            owner_id, query_hash, (item.record.record_id for item in hits), outcome
        )
        return RetrievalResult(
            owner_id,
            outcome,
            tuple(hits),
            query_hash,
            excluded_count=max(0, len(all_records) - len(eligible)),
        )

    def profile_records(
        self, owner_id: str, profile: Mapping[str, Any]
    ) -> tuple[MemoryRecord, ...]:
        """Adapt legacy profile facts into core records without persisting them.

        Runtime settings stay profile settings.  Only ordinary user facts cross
        this compatibility boundary, where the same retrieval policy and owner
        isolation as every other memory record applies.
        """
        owner_id = str(owner_id)
        now = _now()
        records: list[MemoryRecord] = []
        for raw_key, value in profile.items():
            key = str(raw_key)
            if (
                key in _PROFILE_RUNTIME_KEYS
                or key.startswith(("_", "proactive_", "last_"))
                or isinstance(value, (dict, list, tuple, set))
                or _SECRET.search(f"{key} {value}")
            ):
                continue
            record_id = (
                "profile-"
                + hashlib.sha256(f"{owner_id}:{key}".encode()).hexdigest()[:24]
            )
            records.append(
                MemoryRecord(
                    record_id=record_id,
                    owner_id=owner_id,
                    tier=MemoryTier.CORE,
                    type="biography",
                    canonical_subject="user",
                    predicate=key,
                    value=value,
                    source="legacy_verified_profile",
                    source_turn="",
                    created_at=now,
                    updated_at=now,
                    valid_from=now,
                    valid_until=None,
                    confidence=1.0,
                    confirmation_state=ConfirmationState.CONFIRMED,
                    importance=default_importance("biography"),
                    sensitivity="ordinary",
                    retention_class="legacy_profile",
                    evidence="Verified profile fact",
                )
            )
        return tuple(records)

    def inspect(
        self, owner_id: str, *, include_tombstones: bool = False
    ) -> list[MemoryRecord]:
        records = self.repository.list_owner(str(owner_id))
        return sorted(
            [item for item in records if include_tombstones or not item.tombstone],
            key=lambda item: (item.updated_at, item.record_id),
            reverse=True,
        )

    def correct(
        self,
        owner_id: str,
        predicate: str,
        value: Any,
        *,
        source_turn: str = "",
        source_channel: str = "unknown",
    ) -> MemoryRecord | None:
        current = next(
            (
                item
                for item in self.inspect(owner_id)
                if _normal(item.predicate) == _normal(predicate)
            ),
            None,
        )
        corrected = self.remember(
            owner_id,
            predicate=predicate,
            value=value,
            type=current.type if current else "biography",
            canonical_subject=current.canonical_subject if current else "user",
            source="explicit_correction",
            source_turn=source_turn,
            source_channel=source_channel,
            evidence=f"Explicit correction of {predicate}",
            tier=current.tier if current else None,
            confidence=1.0,
            importance=current.importance if current else None,
        )
        if current and current.type == "device_alias":
            try:
                from services.smart_home.aliases import reject_device_alias

                reject_device_alias(
                    str(owner_id),
                    current.predicate,
                    provenance="memory_service_explicit_correction",
                )
            except Exception:
                # Memory correction remains authoritative even if an optional
                # operational alias index is temporarily unavailable.
                pass
        return corrected

    def invalidate(self, owner_id: str, record_id: str, *, reason: str = "") -> bool:
        record = self.repository.get(str(owner_id), str(record_id))
        if record is None or record.owner_id != str(owner_id):
            return False
        self.repository.upsert(
            replace(
                record,
                updated_at=_now(),
                confirmation_state=ConfirmationState.INVALIDATED,
                tombstone=True,
                metadata={
                    **dict(record.metadata),
                    "invalidation_reason": str(reason)[:180],
                },
            )
        )
        return True

    def forget(
        self,
        owner_id: str,
        *,
        record_id: str | None = None,
        predicate: str | None = None,
    ) -> int:
        selected = self.inspect(owner_id)
        if record_id:
            selected = [item for item in selected if item.record_id == str(record_id)]
        elif predicate:
            selected = [
                item
                for item in selected
                if _normal(item.predicate) == _normal(predicate)
            ]
        count = 0
        for record in selected:
            if self.invalidate(owner_id, record.record_id, reason="user_forget"):
                count += 1
        return count

    def export(self, owner_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "owner_id": str(owner_id),
            "exported_at": _iso(_now()),
            "memories": [
                item.to_document()
                for item in self.inspect(owner_id, include_tombstones=True)
            ],
        }

    def record_feedback(
        self, owner_id: str, signal: str, value: Any, *, source_turn: str = ""
    ) -> MemoryRecord | None:
        return self.remember(
            owner_id,
            predicate=f"adaptation_{_normal(signal).replace(' ', '_')[:80]}",
            value=value,
            type="feedback",
            canonical_subject="assistant_behavior",
            source="user_feedback",
            source_turn=source_turn,
            evidence=f"Explicit user feedback: {signal}"[:500],
            tier=MemoryTier.PROCEDURAL,
            confidence=1.0,
            retention_class="adaptation",
        )

    def explain_provenance(
        self, owner_id: str, record_id: str
    ) -> dict[str, Any] | None:
        record = self.repository.get(str(owner_id), str(record_id))
        if record is None or record.owner_id != str(owner_id):
            return None
        return {
            "record_id": record.record_id,
            "source": record.source,
            "source_turn": record.source_turn,
            "source_channel": record.source_channel,
            "evidence": record.evidence,
            "confirmation_state": record.confirmation_state.value,
            "confidence": record.confidence,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
        }

    def consolidate(self, owner_id: str, *, dry_run: bool = True) -> ConsolidationPlan:
        now = _now()
        records = self.inspect(owner_id, include_tombstones=False)
        actions: list[ConsolidationAction] = []
        best: dict[tuple[str, str, str], MemoryRecord] = {}
        needs_compaction = False
        for record in records:
            if record.valid_until and record.valid_until <= now:
                actions.append(
                    ConsolidationAction(
                        "expire", record.record_id, "retention window ended"
                    )
                )
                needs_compaction = True
                continue
            key = (
                _normal(record.canonical_subject),
                _normal(record.predicate),
                json.dumps(record.value, sort_keys=True, default=str),
            )
            prior = best.get(key)
            if prior is None or (
                record.confirmation_count,
                record.confidence,
                record.updated_at,
            ) > (
                prior.confirmation_count,
                prior.confidence,
                prior.updated_at,
            ):
                if prior is not None:
                    actions.append(
                        ConsolidationAction(
                            "deduplicate",
                            prior.record_id,
                            f"duplicate of {record.record_id}",
                        )
                    )
                    needs_compaction = True
                best[key] = record
            else:
                actions.append(
                    ConsolidationAction(
                        "deduplicate",
                        record.record_id,
                        f"duplicate of {prior.record_id}",
                    )
                )
                needs_compaction = True
            if (
                record.confirmation_state is ConfirmationState.CANDIDATE
                and now - record.updated_at > timedelta(days=14)
            ):
                actions.append(
                    ConsolidationAction(
                        "decay_candidate",
                        record.record_id,
                        "unconfirmed hypothesis is stale",
                    )
                )
            if record.type == "episode" and len(str(record.value)) > 280:
                actions.append(
                    ConsolidationAction(
                        "summarize_episode",
                        record.record_id,
                        "long episode has a deterministic compact excerpt",
                    )
                )
        if needs_compaction:
            actions.append(
                ConsolidationAction(
                    "compact_index",
                    "owner-index",
                    "dedupe or expiry changed retrieval indexes",
                )
            )
        if not dry_run:
            for action in actions:
                if action.action == "compact_index":
                    self.repository.compact(str(owner_id))
                    continue
                record = self.repository.get(str(owner_id), action.record_id)
                if record is None:
                    continue
                if action.action in {"expire", "deduplicate"}:
                    self.invalidate(owner_id, action.record_id, reason=action.action)
                elif action.action == "decay_candidate":
                    self.repository.upsert(
                        replace(
                            record,
                            updated_at=now,
                            confidence=max(0.0, record.confidence - 0.05),
                        )
                    )
                elif action.action == "summarize_episode":
                    compact = re.split(r"(?<=[.!?])\s+", str(record.value), maxsplit=1)[
                        0
                    ][:240]
                    self.repository.upsert(
                        replace(
                            record,
                            updated_at=now,
                            metadata={
                                **dict(record.metadata),
                                "consolidated_summary": compact,
                                "consolidated_at": _iso(now),
                            },
                        )
                    )
        return ConsolidationPlan(str(owner_id), dry_run, tuple(actions))

    def migrate_owner(self, owner_id: str, *, dry_run: bool = True) -> MigrationReport:
        owner_id = str(owner_id)
        run_id = uuid.uuid4().hex
        rollback_until = _now() + timedelta(
            days=max(1, int(os.getenv("MEMORY_MIGRATION_ROLLBACK_DAYS", "30")))
        )
        source = self.repository.legacy_documents(owner_id)
        backup = tuple(
            item.to_document() for item in self.repository.list_owner(owner_id)
        )
        records = [MemoryRecord.from_document(item) for item in source]
        mismatches = tuple(
            item.record_id for item in records if item.owner_id != owner_id
        )
        migrated = 0
        if not dry_run and not mismatches:
            self.repository.save_migration_backup(
                run_id, owner_id, backup, rollback_until
            )
            for record in records:
                self.repository.upsert(record)
                migrated += 1
        target = self.repository.list_owner(owner_id)
        target_ids = {item.record_id for item in target}
        source_ids = {item.record_id for item in records if item.owner_id == owner_id}
        missing = tuple(sorted(source_ids - target_ids))
        report = MigrationReport(
            run_id=run_id,
            owner_id=owner_id,
            dry_run=dry_run,
            source_count=len(source),
            migrated_count=migrated,
            target_count=len(target),
            missing_record_ids=missing,
            ownership_mismatches=mismatches,
            backup_documents=backup,
            rollback_until=rollback_until,
        )
        if not dry_run and report.valid:
            self.repository.set_cutover_mode(owner_id, "unified")
        return report

    def rollback_migration(self, report: MigrationReport) -> int:
        if _now() > report.rollback_until:
            raise ValueError("memory migration rollback window has expired")
        restored = 0
        for document in report.backup_documents:
            record = MemoryRecord.from_document(document)
            if record.owner_id != report.owner_id:
                continue
            self.repository.upsert(record)
            restored += 1
        self.repository.set_cutover_mode(report.owner_id, "legacy")
        return restored

    def cutover_owner(self, owner_id: str, *, unified: bool) -> str:
        mode = "unified" if unified else "legacy"
        self.repository.set_cutover_mode(str(owner_id), mode)
        return mode

    def inventory(
        self, owner_id: str, *, profile: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        legacy = self.repository.legacy_documents(str(owner_id))
        unified = self.repository.list_owner(str(owner_id))
        return {
            "owner_id": str(owner_id),
            "repository": type(self.repository).__name__,
            "cutover_mode": self.repository.cutover_mode(str(owner_id)),
            "legacy_record_count": len(legacy),
            "unified_record_count": len(unified),
            "legacy_shapes": sorted(
                {tuple(sorted(str(key) for key in item)) for item in legacy},
                key=lambda item: (len(item), item),
            ),
            "tiers": {
                tier.value: sum(item.tier is tier for item in unified)
                for tier in MemoryTier
            },
            "postgres_profile_fact_count": len(
                self.profile_records(str(owner_id), profile or {})
            ),
        }

    def shadow_compare(self, owner_id: str) -> dict[str, Any]:
        legacy = {
            str(item.get("record_id") or item.get("id") or item.get("_id"))
            for item in self.repository.legacy_documents(str(owner_id))
        }
        unified = {item.record_id for item in self.repository.list_owner(str(owner_id))}
        return {
            "owner_id": str(owner_id),
            "legacy_count": len(legacy),
            "unified_count": len(unified),
            "missing_from_unified": sorted(legacy - unified),
            "extra_in_unified": sorted(unified - legacy),
            "match": legacy == unified,
        }

    def shadow_compare_retrieval(
        self, owner_id: str, queries: Iterable[str], *, limit: int = 8
    ) -> dict[str, Any]:
        owner_id = str(owner_id)
        legacy_documents = [
            MemoryRecord.from_document(item).to_document()
            for item in self.repository.legacy_documents(owner_id)
            if str(item.get("owner_id") or item.get("internal_id") or "") == owner_id
        ]
        comparisons: list[dict[str, Any]] = []
        for query in queries:
            legacy_ids = [
                str(item.get("record_id") or item.get("id") or item.get("_id"))
                for item in rank_memories(
                    str(query),
                    legacy_documents,
                    limit=limit,
                    explicit_search=True,
                )
            ]
            unified_ids = [
                item.record.record_id
                for item in self.retrieve(
                    owner_id,
                    str(query),
                    limit=limit,
                    explicit_search=True,
                ).hits
            ]
            comparisons.append(
                {
                    "query_hash": hashlib.sha256(
                        str(query).casefold().encode()
                    ).hexdigest(),
                    "legacy_ids": legacy_ids,
                    "unified_ids": unified_ids,
                    "match": legacy_ids == unified_ids,
                }
            )
        return {
            "owner_id": owner_id,
            "comparisons": comparisons,
            "match": all(item["match"] for item in comparisons),
        }


_SERVICE_LOCK = threading.Lock()
_SERVICE: MemoryService | None = None
_SERVICE_SIGNATURE: tuple[str, int] | tuple[str] | None = None


def get_memory_service(
    *, reset: bool = False, mongo_database: Any | None = None
) -> MemoryService:
    global _SERVICE, _SERVICE_SIGNATURE
    desired_signature: tuple[str, int] | tuple[str] = (
        ("mongo", id(mongo_database)) if os.getenv("MONGODB_URI") else ("sqlite",)
    )
    if reset:
        with _SERVICE_LOCK:
            _SERVICE = None
            _SERVICE_SIGNATURE = None
    if _SERVICE is None or _SERVICE_SIGNATURE != desired_signature:
        with _SERVICE_LOCK:
            if _SERVICE is None or _SERVICE_SIGNATURE != desired_signature:
                repository: MemoryRepository = (
                    MongoMemoryRepository(mongo_database)
                    if os.getenv("MONGODB_URI")
                    else SQLiteMemoryRepository()
                )
                _SERVICE = MemoryService(repository)
                _SERVICE_SIGNATURE = desired_signature
    return _SERVICE


__all__ = [
    "ConfirmationState",
    "ConsolidationAction",
    "ConsolidationPlan",
    "MemoryRecord",
    "MemoryRepository",
    "MemoryService",
    "MemoryTier",
    "MigrationReport",
    "RetrievalHit",
    "RetrievalResult",
    "WriteDecision",
    "get_memory_service",
]
