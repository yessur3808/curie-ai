"""Backend-neutral persistence repositories used by runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import threading
from typing import Any, Protocol
import uuid


class IdentityRepository(Protocol):
    def get_or_create(self, channel: str, external_id: str, **metadata: Any) -> str: ...
    def get_external_id(self, internal_id: str, channel: str) -> str | None: ...


class ProfileRepository(Protocol):
    def get(self, internal_id: str) -> dict: ...
    def update(self, internal_id: str, facts: dict) -> None: ...
    def list_with_identities(self) -> list[dict]: ...


class SessionRepository(Protocol):
    def get_history(self, platform: str, internal_id: str) -> list[dict]: ...

    def add_message(
        self, platform: str, internal_id: str, role: str, content: str
    ) -> None: ...
    def reset_session(self, platform: str, internal_id: str) -> None: ...


class ApprovalRepository(Protocol):
    def create(self, internal_id: str, action: dict, ttl_minutes: int = 30) -> str: ...
    def consume(self, internal_id: str, token: str, approve: bool) -> dict | None: ...


class MutationRepository(Protocol):
    def reserve(
        self,
        internal_id: str,
        idempotency_key: str,
        capability: str,
        request_hash: str,
    ) -> dict | None: ...

    def finish(
        self,
        internal_id: str,
        idempotency_key: str,
        status: str,
        receipt: dict | None = None,
    ) -> None: ...


class AuditRepository(Protocol):
    def append(
        self, internal_id: str, action: str, status: str, details: dict
    ) -> None: ...
    def list(self, internal_id: str, limit: int = 50) -> list[dict]: ...
    def delete_owner(self, internal_id: str) -> int: ...


class ReminderRepository(Protocol):
    def create(
        self, internal_id: str, platform: str, message: str, due_at: datetime
    ) -> Any: ...
    def upcoming(self, internal_id: str, now: datetime) -> list[dict]: ...
    def delete(self, internal_id: str, reminder_id: Any | None = None) -> int: ...
    def due(self, now: datetime) -> list[dict]: ...
    def mark_fired(self, reminder_id: Any, failed: bool = False) -> None: ...
    def record_attempt(self, reminder_id: Any, now: datetime) -> None: ...


class SQLiteIdentityRepository:
    def get_or_create(self, channel: str, external_id: str, **metadata: Any) -> str:
        from memory.local_store import get_or_create_user

        return get_or_create_user(channel, str(external_id))

    def get_external_id(self, internal_id: str, channel: str) -> str | None:
        from memory.local_store import get_external_id

        return get_external_id(str(internal_id), channel)


class SQLiteProfileRepository:
    def get(self, internal_id: str) -> dict:
        from memory.local_store import get_profile

        return get_profile(str(internal_id))

    def update(self, internal_id: str, facts: dict) -> None:
        from memory.local_store import update_profile

        update_profile(str(internal_id), facts)

    def list_with_identities(self) -> list[dict]:
        from memory.local_store import list_users_with_profiles

        return list_users_with_profiles()


class SQLiteSessionRepository:
    def get_history(self, platform: str, internal_id: str) -> list[dict]:
        from memory.local_store import get_history

        return get_history(platform, str(internal_id))

    def add_message(
        self, platform: str, internal_id: str, role: str, content: str
    ) -> None:
        from memory.local_store import add_message

        add_message(platform, str(internal_id), role, content)

    def reset_session(self, platform: str, internal_id: str) -> None:
        from memory.local_store import reset_history

        reset_history(platform, str(internal_id))


class SQLiteApprovalRepository:
    def create(self, internal_id: str, action: dict, ttl_minutes: int = 30) -> str:
        from memory.local_store import create_pending_action

        return create_pending_action(str(internal_id), action, ttl_minutes)

    def consume(self, internal_id: str, token: str, approve: bool) -> dict | None:
        from memory.local_store import consume_pending_action

        return consume_pending_action(str(internal_id), token, approve)


class SQLiteMutationRepository:
    def reserve(
        self,
        internal_id: str,
        idempotency_key: str,
        capability: str,
        request_hash: str,
    ) -> dict | None:
        from memory.local_store import reserve_mutation_attempt

        return reserve_mutation_attempt(
            str(internal_id), idempotency_key, capability, request_hash
        )

    def finish(
        self,
        internal_id: str,
        idempotency_key: str,
        status: str,
        receipt: dict | None = None,
    ) -> None:
        from memory.local_store import finish_mutation_attempt

        finish_mutation_attempt(str(internal_id), idempotency_key, status, receipt)


class SQLiteAuditRepository:
    def append(self, internal_id: str, action: str, status: str, details: dict) -> None:
        from memory.local_store import append_action_audit
        from services.audit import normalize_audit_details

        append_action_audit(
            str(internal_id), action, status, normalize_audit_details(details)
        )

    def list(self, internal_id: str, limit: int = 50) -> list[dict]:
        from memory.local_store import list_action_audit

        return list_action_audit(str(internal_id), limit)

    def delete_owner(self, internal_id: str) -> int:
        from memory.local_store import delete_action_audit

        return delete_action_audit(str(internal_id))


class SQLiteReminderRepository:
    def create(
        self, internal_id: str, platform: str, message: str, due_at: datetime
    ) -> Any:
        from memory.local_store import create_reminder

        return create_reminder(internal_id, platform, message, due_at)

    def upcoming(self, internal_id: str, now: datetime) -> list[dict]:
        from memory.local_store import list_reminders

        return list_reminders(internal_id, now)

    def delete(self, internal_id: str, reminder_id: Any | None = None) -> int:
        from memory.local_store import delete_reminder

        return delete_reminder(internal_id, reminder_id)

    def due(self, now: datetime) -> list[dict]:
        from memory.local_store import due_reminders

        return due_reminders(now)

    def mark_fired(self, reminder_id: Any, failed: bool = False) -> None:
        from memory.local_store import mark_reminder

        mark_reminder(str(reminder_id), fired=True, failed=failed)

    def record_attempt(self, reminder_id: Any, now: datetime) -> None:
        from memory.local_store import mark_reminder

        mark_reminder(str(reminder_id), attempted_at=now)


class ExternalIdentityRepository:
    def get_or_create(self, channel: str, external_id: str, **metadata: Any) -> str:
        from memory.users import UserManager

        return UserManager.get_or_create_user_internal_id(
            channel,
            external_id,
            secret_username=metadata.get("secret_username"),
            updated_by=metadata.get("updated_by"),
            is_master=metadata.get("is_master", False),
            roles=metadata.get("roles"),
            display_name=metadata.get("display_name"),
        )

    def get_external_id(self, internal_id: str, channel: str) -> str | None:
        from memory.users import UserManager

        return UserManager.get_external_id(internal_id, channel)


class ExternalProfileRepository:
    def get(self, internal_id: str) -> dict:
        from memory.users import UserManager

        return UserManager.get_user_profile(internal_id)

    def update(self, internal_id: str, facts: dict) -> None:
        from memory.users import UserManager

        UserManager.update_user_profile(internal_id, facts)

    def list_with_identities(self) -> list[dict]:
        from memory.database import get_pg_conn, mongo_db

        users = []
        for profile in mongo_db.user_profiles.find({}):
            internal_id = str(profile.get("_id", ""))
            if not internal_id:
                continue
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT telegram_id,discord_id,whatsapp_id,api_id FROM users WHERE internal_id=%s",
                    (internal_id,),
                )
                row = cur.fetchone()
            row = dict(row) if row else {}
            for platform in ("telegram", "discord", "whatsapp", "api"):
                values = row.get(f"{platform}_id") or []
                if not isinstance(values, (list, tuple)):
                    values = [values]
                for external_id in values:
                    if external_id:
                        users.append(
                            {
                                "platform": platform,
                                "external_user_id": str(external_id),
                                "internal_id": internal_id,
                                "facts": profile.get("facts", {}),
                            }
                        )
        return users


class ExternalSessionRepository:
    def _manager(self):
        from memory.session_manager import SessionManager

        return SessionManager(
            mongo_uri=os.environ["MONGODB_URI"],
            db_name=os.getenv("MONGODB_DB", "assistant_db"),
            collection_name=os.getenv("SESSION_COLLECTION", "sessions"),
        )

    def get_history(self, platform: str, internal_id: str) -> list[dict]:
        manager = self._manager()
        try:
            return manager.get_history(platform, internal_id)
        finally:
            manager.close()

    def add_message(
        self, platform: str, internal_id: str, role: str, content: str
    ) -> None:
        manager = self._manager()
        try:
            manager.add_message(platform, internal_id, role, content)
        finally:
            manager.close()

    def reset_session(self, platform: str, internal_id: str) -> None:
        manager = self._manager()
        try:
            manager.reset_session(platform, internal_id)
        finally:
            manager.close()


class ExternalApprovalRepository:
    def create(self, internal_id: str, action: dict, ttl_minutes: int = 30) -> str:
        from memory.database import mongo_db

        token = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc)
        mongo_db.pending_actions.insert_one(
            {
                "token": token,
                "internal_id": str(internal_id),
                "status": "pending",
                "action": action,
                "created_at": now,
                "expires_at": now + timedelta(minutes=ttl_minutes),
            }
        )
        return token

    def consume(self, internal_id: str, token: str, approve: bool) -> dict | None:
        from pymongo import ReturnDocument
        from memory.database import mongo_db

        row = mongo_db.pending_actions.find_one_and_update(
            {
                "token": token.lower(),
                "internal_id": str(internal_id),
                "status": "pending",
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            },
            {"$set": {"status": "approved" if approve else "rejected"}},
            return_document=ReturnDocument.BEFORE,
        )
        return row.get("action") if row else None


class ExternalMutationRepository:
    def reserve(
        self,
        internal_id: str,
        idempotency_key: str,
        capability: str,
        request_hash: str,
    ) -> dict | None:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError
        from memory.database import mongo_db

        now = datetime.now(timezone.utc)
        query = {
            "internal_id": str(internal_id),
            "idempotency_key": str(idempotency_key),
        }
        try:
            row = mongo_db.mutation_attempts.find_one_and_update(
                query,
                {
                    "$setOnInsert": {
                        "capability": capability,
                        "request_hash": request_hash,
                        "status": "started",
                        "receipt": None,
                        "created_at": now,
                    },
                    "$set": {"updated_at": now},
                },
                upsert=True,
                return_document=ReturnDocument.BEFORE,
            )
        except DuplicateKeyError:
            # Another worker won the same atomic reservation between our read
            # and upsert. Treat it as an existing attempt, never a reason to
            # execute the mutation twice.
            row = mongo_db.mutation_attempts.find_one(query)
        return dict(row) if row else None

    def finish(
        self,
        internal_id: str,
        idempotency_key: str,
        status: str,
        receipt: dict | None = None,
    ) -> None:
        from memory.database import mongo_db

        mongo_db.mutation_attempts.update_one(
            {
                "internal_id": str(internal_id),
                "idempotency_key": str(idempotency_key),
            },
            {
                "$set": {
                    "status": status,
                    "receipt": receipt,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )


class ExternalAuditRepository:
    def append(self, internal_id: str, action: str, status: str, details: dict) -> None:
        from memory.database import mongo_db

        from services.audit import normalize_audit_details

        retention_days = max(1, int(os.getenv("CURIE_AUDIT_RETENTION_DAYS", "90")))
        mongo_db.action_audit.delete_many(
            {
                "created_at": {
                    "$lt": datetime.now(timezone.utc) - timedelta(days=retention_days)
                }
            }
        )
        mongo_db.action_audit.insert_one(
            {
                "internal_id": str(internal_id),
                "action": action,
                "status": status,
                "details": normalize_audit_details(details),
                "created_at": datetime.now(timezone.utc),
            }
        )

    def list(self, internal_id: str, limit: int = 50) -> list[dict]:
        from memory.database import mongo_db

        cursor = (
            mongo_db.action_audit.find({"internal_id": str(internal_id)}, {"_id": 0})
            .sort("created_at", -1)
            .limit(int(limit))
        )
        return list(cursor)

    def delete_owner(self, internal_id: str) -> int:
        from memory.database import mongo_db

        return int(
            mongo_db.action_audit.delete_many(
                {"internal_id": str(internal_id)}
            ).deleted_count
        )


class ExternalReminderRepository:
    def create(
        self, internal_id: str, platform: str, message: str, due_at: datetime
    ) -> Any:
        from memory.database import mongo_db

        result = mongo_db.reminders.insert_one(
            {
                "internal_id": internal_id,
                "platform": platform,
                "message": message,
                "due_at": due_at,
                "created_at": datetime.now(timezone.utc),
                "fired": False,
                "snooze_count": 0,
            }
        )
        return result.inserted_id

    def upcoming(self, internal_id: str, now: datetime) -> list[dict]:
        from memory.database import mongo_db

        return list(
            mongo_db.reminders.find(
                {"internal_id": internal_id, "fired": False, "due_at": {"$gte": now}},
                sort=[("due_at", 1)],
            )
        )

    def delete(self, internal_id: str, reminder_id: Any | None = None) -> int:
        from memory.database import mongo_db

        query = {"internal_id": internal_id, "fired": False}
        if reminder_id is not None:
            query["_id"] = reminder_id
            return mongo_db.reminders.delete_one(query).deleted_count
        return mongo_db.reminders.delete_many(query).deleted_count

    def due(self, now: datetime) -> list[dict]:
        from memory.database import mongo_db

        return list(mongo_db.reminders.find({"fired": False, "due_at": {"$lte": now}}))

    def mark_fired(self, reminder_id: Any, failed: bool = False) -> None:
        from memory.database import mongo_db

        values = {"fired": True}
        if failed:
            values["delivery_failed"] = True
        mongo_db.reminders.update_one({"_id": reminder_id}, {"$set": values})

    def record_attempt(self, reminder_id: Any, now: datetime) -> None:
        from memory.database import mongo_db

        mongo_db.reminders.update_one(
            {"_id": reminder_id},
            {"$inc": {"attempt_count": 1}, "$set": {"last_attempt_at": now}},
        )


@dataclass(frozen=True, slots=True)
class PersistenceRepositories:
    identities: IdentityRepository
    profiles: ProfileRepository
    sessions: SessionRepository
    approvals: ApprovalRepository
    mutations: MutationRepository
    audits: AuditRepository
    reminders: ReminderRepository
    backend: str


_instance: PersistenceRepositories | None = None
_instance_key: tuple[bool, str] | None = None
_lock = threading.Lock()


def get_repositories() -> PersistenceRepositories:
    global _instance, _instance_key
    external = bool(os.getenv("POSTGRES_HOST") and os.getenv("MONGODB_URI"))
    if external:
        key = (True, "external")
    else:
        from memory import local_store

        key = (False, str(local_store._PATH))
    if _instance is None or _instance_key != key:
        with _lock:
            if _instance is None or _instance_key != key:
                if external:
                    _instance = PersistenceRepositories(
                        ExternalIdentityRepository(),
                        ExternalProfileRepository(),
                        ExternalSessionRepository(),
                        ExternalApprovalRepository(),
                        ExternalMutationRepository(),
                        ExternalAuditRepository(),
                        ExternalReminderRepository(),
                        "external",
                    )
                else:
                    _instance = PersistenceRepositories(
                        SQLiteIdentityRepository(),
                        SQLiteProfileRepository(),
                        SQLiteSessionRepository(),
                        SQLiteApprovalRepository(),
                        SQLiteMutationRepository(),
                        SQLiteAuditRepository(),
                        SQLiteReminderRepository(),
                        "sqlite",
                    )
                _instance_key = key
    return _instance


def reset_repositories() -> None:
    global _instance, _instance_key
    with _lock:
        _instance = None
        _instance_key = None
