"""Zero-configuration durable SQLite memory fallback."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid

_PATH = Path(os.getenv("CURIE_LOCAL_MEMORY_DB", ".curie_memory.sqlite3"))
_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_PATH, timeout=10, check_same_thread=False)
    try:
        os.chmod(_PATH, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            channel TEXT NOT NULL, external_id TEXT NOT NULL, internal_id TEXT NOT NULL,
            PRIMARY KEY(channel, external_id)
        );
        CREATE TABLE IF NOT EXISTS profiles (
            internal_id TEXT PRIMARY KEY, facts_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
            internal_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_metadata (
            platform TEXT NOT NULL, internal_id TEXT NOT NULL,
            key TEXT NOT NULL, value_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(platform, internal_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_session_metadata_owner
            ON session_metadata(internal_id, platform, updated_at);
        CREATE TABLE IF NOT EXISTS adaptive_memories (
            id TEXT PRIMARY KEY, internal_id TEXT NOT NULL, document_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learned_abilities (
            id TEXT PRIMARY KEY, internal_id TEXT NOT NULL, status TEXT NOT NULL,
            document_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS adaptation_profiles (
            internal_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
            document_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS adaptation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, internal_id TEXT NOT NULL,
            signal TEXT NOT NULL, document_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_adaptation_events_owner_signal
            ON adaptation_events(internal_id, signal, created_at);
        CREATE TABLE IF NOT EXISTS routing_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, internal_id TEXT NOT NULL,
            decision_id TEXT NOT NULL, request_hash TEXT NOT NULL,
            document_json TEXT NOT NULL, corrected_capability TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_routing_outcomes_owner
            ON routing_outcomes(internal_id, created_at);
        CREATE TABLE IF NOT EXISTS durable_tasks (
            id TEXT PRIMARY KEY, internal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, status TEXT NOT NULL,
            document_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(internal_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_durable_tasks_owner_status
            ON durable_tasks(internal_id, status, updated_at);
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, internal_id TEXT NOT NULL,
            document_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_actions (
            token TEXT PRIMARY KEY, internal_id TEXT NOT NULL, status TEXT NOT NULL,
            action_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS action_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, internal_id TEXT NOT NULL,
            action TEXT NOT NULL, status TEXT NOT NULL, details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY, internal_id TEXT NOT NULL, platform TEXT NOT NULL,
            message TEXT NOT NULL, due_at TEXT NOT NULL, created_at TEXT NOT NULL,
            fired INTEGER NOT NULL DEFAULT 0, delivery_failed INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_reminders_due
            ON reminders(fired, due_at);
        CREATE TABLE IF NOT EXISTS personal_items (
            id TEXT PRIMARY KEY, internal_id TEXT NOT NULL, kind TEXT NOT NULL,
            document_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_personal_items_owner_kind
            ON personal_items(internal_id, kind, updated_at);
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
        );
        """
    )
    from memory.schema_migrations import apply_migrations

    apply_migrations(conn)
    return conn


@contextmanager
def _managed_connection():
    """Commit/rollback like sqlite's context manager, then always close."""
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def save_personal_item(internal_id: str, kind: str, document: dict) -> dict:
    item = dict(document)
    source_id = str(item.get("id") or uuid.uuid4().hex)
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK, _managed_connection() as conn:
        owned = conn.execute(
            "SELECT created_at FROM personal_items WHERE id=? AND internal_id=?",
            (source_id, str(internal_id)),
        ).fetchone()
        item["id"] = (
            source_id
            if owned
            else __import__("hashlib")
            .sha256(f"{internal_id}:{source_id}".encode())
            .hexdigest()[:32]
        )
        existing = conn.execute(
            "SELECT created_at FROM personal_items WHERE id=? AND internal_id=?",
            (item["id"], str(internal_id)),
        ).fetchone()
        conn.execute(
            "INSERT INTO personal_items(id,internal_id,kind,document_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document_json=excluded.document_json,updated_at=excluded.updated_at",
            (
                item["id"],
                str(internal_id),
                str(kind),
                json.dumps(item, default=str),
                existing["created_at"] if existing else now,
                now,
            ),
        )
    return item


def list_personal_items(internal_id: str, kind: str | None = None) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        if kind:
            rows = conn.execute(
                "SELECT document_json FROM personal_items WHERE internal_id=? AND kind=? ORDER BY updated_at",
                (str(internal_id), str(kind)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT document_json FROM personal_items WHERE internal_id=? ORDER BY updated_at",
                (str(internal_id),),
            ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]


def delete_personal_item(internal_id: str, item_id: str) -> int:
    with _LOCK, _managed_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM personal_items WHERE internal_id=? AND id=?",
            (str(internal_id), str(item_id)),
        )
        return int(cursor.rowcount)


def get_or_create_user(channel: str, external_id: str) -> str:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT internal_id FROM users WHERE channel=? AND external_id=?",
            (channel, str(external_id)),
        ).fetchone()
        if row:
            return row["internal_id"]
        internal_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users(channel, external_id, internal_id) VALUES(?,?,?)",
            (channel, str(external_id), internal_id),
        )
        update_profile(
            internal_id,
            {
                "proactive_messaging_enabled": False,
                "proactive_interval_hours": 24,
                "proactive_predictions_enabled": True,
                "proactive_quiet_hours": {"start": 22, "end": 8},
                "proactive_daily_max": 1,
                "proactive_weekly_max": 7,
                "proactive_topic_cooldown_hours": 72,
            },
            conn=conn,
        )
        return internal_id


def get_profile(internal_id: str) -> dict:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT facts_json FROM profiles WHERE internal_id=?", (str(internal_id),)
        ).fetchone()
        return json.loads(row["facts_json"]) if row else {}


def update_profile(internal_id: str, facts: dict, conn=None) -> None:
    owns = conn is None
    connection = conn or _connect()
    try:
        row = connection.execute(
            "SELECT facts_json FROM profiles WHERE internal_id=?", (str(internal_id),)
        ).fetchone()
        current = json.loads(row["facts_json"]) if row else {}
        current.update(facts)
        connection.execute(
            "INSERT INTO profiles(internal_id,facts_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(internal_id) DO UPDATE SET facts_json=excluded.facts_json, "
            "updated_at=excluded.updated_at",
            (
                str(internal_id),
                json.dumps(current, default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    finally:
        if owns:
            connection.close()


def delete_profile_facts(
    internal_id: str,
    keys: list[str] | tuple[str, ...] | set[str],
) -> int:
    """Remove selected profile facts without disturbing runtime preferences."""
    clean_keys = {str(key) for key in keys if str(key)}
    if not clean_keys:
        return 0
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT facts_json FROM profiles WHERE internal_id=?", (str(internal_id),)
        ).fetchone()
        if not row:
            return 0
        current = json.loads(row["facts_json"])
        removed = sum(key in current for key in clean_keys)
        if not removed:
            return 0
        for key in clean_keys:
            current.pop(key, None)
        conn.execute(
            "UPDATE profiles SET facts_json=?, updated_at=? WHERE internal_id=?",
            (
                json.dumps(current, default=str),
                datetime.now(timezone.utc).isoformat(),
                str(internal_id),
            ),
        )
        return removed


def list_users_with_profiles() -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT u.channel,u.external_id,u.internal_id,p.facts_json FROM users u "
            "LEFT JOIN profiles p ON p.internal_id=u.internal_id"
        ).fetchall()
        return [
            {
                "platform": row["channel"],
                "external_user_id": row["external_id"],
                "internal_id": row["internal_id"],
                "facts": json.loads(row["facts_json"] or "{}"),
            }
            for row in rows
        ]


def get_external_id(internal_id: str, channel: str) -> str | None:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT external_id FROM users WHERE internal_id=? AND channel=? "
            "ORDER BY rowid LIMIT 1",
            (str(internal_id), channel),
        ).fetchone()
        return str(row["external_id"]) if row else None


def add_message(platform: str, internal_id: str, role: str, content: str) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO messages(platform,internal_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (
                platform,
                str(internal_id),
                role,
                content,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_history(platform: str, internal_id: str, limit: int = 100) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT role,content,created_at FROM messages WHERE platform=? AND internal_id=? "
            "ORDER BY id DESC LIMIT ?",
            (platform, str(internal_id), int(limit)),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]


def reset_history(platform: str, internal_id: str) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "DELETE FROM messages WHERE platform=? AND internal_id=?",
            (platform, str(internal_id)),
        )
        # A reset starts a genuinely fresh conversation. Keep unrelated session
        # preferences, but never carry a model-generated working summary into it.
        conn.execute(
            "DELETE FROM session_metadata "
            "WHERE platform=? AND internal_id=? AND key='working_context_v1'",
            (platform, str(internal_id)),
        )


def get_session_metadata(platform: str, internal_id: str) -> dict:
    """Return owner/session-scoped metadata without exposing other sessions."""
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT key,value_json FROM session_metadata "
            "WHERE platform=? AND internal_id=?",
            (str(platform), str(internal_id)),
        ).fetchall()
    result = {}
    for row in rows:
        try:
            result[str(row["key"])] = json.loads(row["value_json"])
        except (TypeError, ValueError):
            continue
    return result


def set_session_metadata(
    platform: str, internal_id: str, key: str, value: object
) -> None:
    """Persist one JSON-safe metadata value for a single conversation session."""
    clean_key = str(key).strip()
    if not clean_key or len(clean_key) > 96:
        raise ValueError("Session metadata key must be between 1 and 96 characters")
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO session_metadata(platform,internal_id,key,value_json,updated_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(platform,internal_id,key) DO UPDATE SET "
            "value_json=excluded.value_json,updated_at=excluded.updated_at",
            (
                str(platform),
                str(internal_id),
                clean_key,
                json.dumps(value, default=str),
                now,
            ),
        )


def purge_expired_records(
    policy: dict[str, int], *, owner_id: str | None = None
) -> dict[str, int]:
    """Apply visible retention limits to content and operational records."""
    now = datetime.now(timezone.utc)
    owner_clause = " AND internal_id=?" if owner_id is not None else ""
    owner_args = (str(owner_id),) if owner_id is not None else ()
    targets = {
        "messages": ("messages", "created_at", "messages"),
        "audit": ("action_audit", "created_at", "audit"),
        "completed_tasks": ("durable_tasks", "updated_at", "completed_tasks"),
        "operational_events": ("routing_outcomes", "created_at", "operational_events"),
    }
    removed: dict[str, int] = {}
    with _LOCK, _managed_connection() as conn:
        for output, (table, timestamp, policy_name) in targets.items():
            days = max(0, int(policy.get(policy_name, 0)))
            cutoff = datetime.fromtimestamp(
                now.timestamp() - days * 86400, timezone.utc
            ).isoformat()
            status_clause = (
                " AND status IN ('completed','failed','cancelled','expired')"
                if table == "durable_tasks"
                else ""
            )
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {timestamp} < ?{owner_clause}{status_clause}",
                (cutoff, *owner_args),
            )
            removed[output] = int(cursor.rowcount)
        # Expired/consumed approvals carry authority and should not linger.
        approval_cutoff = now.isoformat()
        cursor = conn.execute(
            "DELETE FROM pending_actions WHERE (expires_at < ? OR status != 'pending')"
            + owner_clause,
            (approval_cutoff, *owner_args),
        )
        removed["approvals"] = int(cursor.rowcount)
    return removed


class LocalSessionManager:
    def get_history(self, platform: str, internal_id: str):
        return get_history(platform, internal_id)

    def add_message(self, platform: str, internal_id: str, role: str, content: str):
        add_message(platform, internal_id, role, content)

    def reset_session(self, platform: str, internal_id: str):
        reset_history(platform, internal_id)

    def get_metadata(self, platform: str, internal_id: str):
        return get_session_metadata(platform, internal_id)

    def set_metadata(self, platform: str, internal_id: str, key: str, value):
        set_session_metadata(platform, internal_id, key, value)

    def close(self):
        return None


def upsert_adaptive_memory(doc: dict) -> None:
    with _LOCK, _managed_connection() as conn:
        old = conn.execute(
            "SELECT document_json FROM adaptive_memories WHERE id=?", (doc["_id"],)
        ).fetchone()
        if old:
            previous = json.loads(old["document_json"])
            doc["confirmation_count"] = int(previous.get("confirmation_count", 0)) + 1
        else:
            doc["confirmation_count"] = 1
        conn.execute(
            "INSERT INTO adaptive_memories(id,internal_id,document_json) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET document_json=excluded.document_json",
            (doc["_id"], doc["internal_id"], json.dumps(doc, default=str)),
        )


def update_adaptive_memory(memory_id: str, internal_id: str, updates: dict) -> bool:
    """Update one memory only when it belongs to the requested owner."""
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT document_json FROM adaptive_memories WHERE id=? AND internal_id=?",
            (str(memory_id), str(internal_id)),
        ).fetchone()
        if not row:
            return False
        document = json.loads(row["document_json"])
        document.update(updates)
        conn.execute(
            "UPDATE adaptive_memories SET document_json=? WHERE id=? AND internal_id=?",
            (json.dumps(document, default=str), str(memory_id), str(internal_id)),
        )
        return True


def delete_adaptive_memories(internal_id: str, key: str | None = None) -> int:
    """Forget owner-scoped memories, optionally limited to one typed key."""
    with _LOCK, _managed_connection() as conn:
        if key is None:
            result = conn.execute(
                "DELETE FROM adaptive_memories WHERE internal_id=?", (str(internal_id),)
            )
            return result.rowcount
        rows = conn.execute(
            "SELECT id,document_json FROM adaptive_memories WHERE internal_id=?",
            (str(internal_id),),
        ).fetchall()
        ids = [
            row["id"]
            for row in rows
            if str(json.loads(row["document_json"]).get("key", "")).casefold()
            == key.casefold()
        ]
        for memory_id in ids:
            conn.execute(
                "DELETE FROM adaptive_memories WHERE id=? AND internal_id=?",
                (memory_id, str(internal_id)),
            )
        return len(ids)


def list_adaptive_memories(internal_id: str) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT document_json FROM adaptive_memories WHERE internal_id=?",
            (str(internal_id),),
        ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]


def upsert_ability(doc: dict) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO learned_abilities(id,internal_id,status,document_json) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status,document_json=excluded.document_json",
            (
                doc["_id"],
                doc["internal_id"],
                doc["status"],
                json.dumps(doc, default=str),
            ),
        )


def update_ability(internal_id: str, skill_id: str, updates: dict) -> bool:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT document_json FROM learned_abilities WHERE id=? AND internal_id=?",
            (str(skill_id), str(internal_id)),
        ).fetchone()
        if not row:
            return False
        document = json.loads(row["document_json"])
        document.update(updates)
        conn.execute(
            "UPDATE learned_abilities SET status=?,document_json=? "
            "WHERE id=? AND internal_id=?",
            (
                document["status"],
                json.dumps(document, default=str),
                str(skill_id),
                str(internal_id),
            ),
        )
        return True


def delete_abilities(internal_id: str, name: str) -> int:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT id,document_json FROM learned_abilities WHERE internal_id=?",
            (str(internal_id),),
        ).fetchall()
        ids = [
            row["id"]
            for row in rows
            if json.loads(row["document_json"]).get("name") == name
        ]
        for skill_id in ids:
            conn.execute(
                "DELETE FROM learned_abilities WHERE id=? AND internal_id=?",
                (skill_id, str(internal_id)),
            )
        return len(ids)


def update_ability_status(internal_id: str, name: str, old: str, new: str) -> bool:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT document_json FROM learned_abilities WHERE id=? AND status=?",
            (f"{internal_id}:{name}", old),
        ).fetchone()
        if not row:
            return False
        doc = json.loads(row["document_json"])
        doc["status"] = new
        conn.execute(
            "UPDATE learned_abilities SET status=?,document_json=? WHERE id=?",
            (new, json.dumps(doc, default=str), f"{internal_id}:{name}"),
        )
        return True


def list_abilities(internal_id: str, status: str | None = None) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        if status is None:
            rows = conn.execute(
                "SELECT document_json FROM learned_abilities WHERE internal_id=?",
                (str(internal_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT document_json FROM learned_abilities WHERE internal_id=? AND status=?",
                (str(internal_id), status),
            ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]


def get_adaptation_profile(internal_id: str) -> dict:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT document_json FROM adaptation_profiles WHERE internal_id=?",
            (str(internal_id),),
        ).fetchone()
        return json.loads(row["document_json"]) if row else {}


def save_adaptation_profile(internal_id: str, document: dict) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO adaptation_profiles(internal_id,version,document_json,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(internal_id) DO UPDATE SET "
            "version=excluded.version,document_json=excluded.document_json,updated_at=excluded.updated_at",
            (
                str(internal_id),
                int(document["version"]),
                json.dumps(document, default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def add_adaptation_event(internal_id: str, signal: str, document: dict) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO adaptation_events(internal_id,signal,document_json,created_at) "
            "VALUES(?,?,?,?)",
            (
                str(internal_id),
                signal,
                json.dumps(document, default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def list_adaptation_events(internal_id: str, signal: str | None = None) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        if signal:
            rows = conn.execute(
                "SELECT signal,document_json,created_at FROM adaptation_events "
                "WHERE internal_id=? AND signal=? ORDER BY id",
                (str(internal_id), signal),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT signal,document_json,created_at FROM adaptation_events "
                "WHERE internal_id=? ORDER BY id",
                (str(internal_id),),
            ).fetchall()
        return [
            {
                "signal": row["signal"],
                **json.loads(row["document_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def reset_adaptation(internal_id: str) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "DELETE FROM adaptation_profiles WHERE internal_id=?", (str(internal_id),)
        )
        conn.execute(
            "DELETE FROM adaptation_events WHERE internal_id=?", (str(internal_id),)
        )


def append_routing_outcome(internal_id: str, request_hash: str, decision: dict) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO routing_outcomes(internal_id,decision_id,request_hash,document_json,created_at) "
            "VALUES(?,?,?,?,?)",
            (
                str(internal_id),
                str(decision["id"]),
                request_hash,
                json.dumps(decision, default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def append_routing_correction(
    internal_id: str, decision_id: str, corrected_capability: str
) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "UPDATE routing_outcomes SET corrected_capability=? "
            "WHERE internal_id=? AND decision_id=?",
            (corrected_capability, str(internal_id), str(decision_id)),
        )


def list_routing_outcomes(internal_id: str) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT decision_id,request_hash,document_json,corrected_capability,created_at "
            "FROM routing_outcomes WHERE internal_id=? ORDER BY id",
            (str(internal_id),),
        ).fetchall()
        return [
            {
                **json.loads(row["document_json"]),
                "request_hash": row["request_hash"],
                "corrected_capability": row["corrected_capability"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def save_durable_task(document: dict) -> None:
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO durable_tasks(id,internal_id,idempotency_key,status,document_json,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=excluded.status,document_json=excluded.document_json,updated_at=excluded.updated_at",
            (
                str(document["id"]),
                str(document["owner_id"]),
                str(document["idempotency_key"]),
                str(document["status"]),
                json.dumps(document, default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_durable_task(internal_id: str, task_id: str) -> dict | None:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT document_json FROM durable_tasks WHERE id=? AND internal_id=?",
            (str(task_id), str(internal_id)),
        ).fetchone()
        return json.loads(row["document_json"]) if row else None


def get_durable_task_by_key(internal_id: str, idempotency_key: str) -> dict | None:
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT document_json FROM durable_tasks "
            "WHERE internal_id=? AND idempotency_key=?",
            (str(internal_id), str(idempotency_key)),
        ).fetchone()
        return json.loads(row["document_json"]) if row else None


def list_durable_tasks(internal_id: str, status: str | None = None) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT document_json FROM durable_tasks WHERE internal_id=? AND status=? "
                "ORDER BY updated_at DESC",
                (str(internal_id), status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT document_json FROM durable_tasks WHERE internal_id=? "
                "ORDER BY updated_at DESC",
                (str(internal_id),),
            ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]


def create_pending_action(internal_id: str, action: dict, ttl_minutes: int = 30) -> str:
    from datetime import timedelta

    token = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO pending_actions(token,internal_id,status,action_json,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                token,
                str(internal_id),
                "pending",
                json.dumps(action, default=str),
                now.isoformat(),
                (now + timedelta(minutes=ttl_minutes)).isoformat(),
            ),
        )
    return token


def consume_pending_action(internal_id: str, token: str, approve: bool) -> dict | None:
    now = datetime.now(timezone.utc)
    with _LOCK, _managed_connection() as conn:
        row = conn.execute(
            "SELECT action_json,expires_at FROM pending_actions "
            "WHERE token=? AND internal_id=? AND status='pending'",
            (token.lower(), str(internal_id)),
        ).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) <= now:
            return None
        conn.execute(
            "UPDATE pending_actions SET status=? WHERE token=?",
            ("approved" if approve else "rejected", token.lower()),
        )
        return json.loads(row["action_json"])


def append_action_audit(
    internal_id: str, action: str, status: str, details: dict
) -> None:
    with _LOCK, _managed_connection() as conn:
        retention_days = max(1, int(os.getenv("CURIE_AUDIT_RETENTION_DAYS", "90")))
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - retention_days * 86400,
            timezone.utc,
        ).isoformat()
        conn.execute("DELETE FROM action_audit WHERE created_at < ?", (cutoff,))
        previous = conn.execute(
            "SELECT details_json FROM action_audit WHERE internal_id=? ORDER BY id DESC LIMIT 1",
            (str(internal_id),),
        ).fetchone()
        previous_hash = "GENESIS"
        if previous:
            previous_hash = json.loads(previous["details_json"]).get(
                "integrity_hash", "UNKNOWN"
            )
        payload = dict(details)
        payload["previous_hash"] = previous_hash
        canonical = json.dumps(
            {
                "internal_id": str(internal_id),
                "action": action,
                "status": status,
                "details": payload,
            },
            default=str,
            sort_keys=True,
        )
        import hashlib

        payload["integrity_hash"] = hashlib.sha256(canonical.encode()).hexdigest()
        conn.execute(
            "INSERT INTO action_audit(internal_id,action,status,details_json,created_at) "
            "VALUES(?,?,?,?,?)",
            (
                str(internal_id),
                action,
                status,
                json.dumps(payload, default=str),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def list_action_audit(internal_id: str, limit: int = 50) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT action,status,details_json,created_at FROM action_audit "
            "WHERE internal_id=? ORDER BY id DESC LIMIT ?",
            (str(internal_id), int(limit)),
        ).fetchall()
        return [
            {
                "action": row["action"],
                "status": row["status"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


def delete_action_audit(internal_id: str) -> int:
    """Controlled owner lifecycle deletion; normal audit APIs remain append-only."""
    with _LOCK, _managed_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM action_audit WHERE internal_id=?", (str(internal_id),)
        )
        return int(cursor.rowcount)


def create_reminder(
    internal_id: str, platform: str, message: str, due_at: datetime
) -> str:
    reminder_id = uuid.uuid4().hex
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            "INSERT INTO reminders(id,internal_id,platform,message,due_at,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                reminder_id,
                str(internal_id),
                platform,
                message,
                due_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return reminder_id


def list_reminders(internal_id: str, now: datetime) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE internal_id=? AND fired=0 AND due_at>=? "
            "ORDER BY due_at",
            (str(internal_id), now.isoformat()),
        ).fetchall()
    return [_reminder_row(row) for row in rows]


def due_reminders(now: datetime) -> list[dict]:
    with _LOCK, _managed_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE fired=0 AND due_at<=? ORDER BY due_at",
            (now.isoformat(),),
        ).fetchall()
    return [_reminder_row(row) for row in rows]


def delete_reminder(internal_id: str, reminder_id: str | None = None) -> int:
    with _LOCK, _managed_connection() as conn:
        if reminder_id is None:
            result = conn.execute(
                "DELETE FROM reminders WHERE internal_id=? AND fired=0",
                (str(internal_id),),
            )
        else:
            result = conn.execute(
                "DELETE FROM reminders WHERE internal_id=? AND id=? AND fired=0",
                (str(internal_id), str(reminder_id)),
            )
        return result.rowcount


def mark_reminder(
    reminder_id: str,
    *,
    fired: bool = False,
    failed: bool = False,
    attempted_at: datetime | None = None,
) -> None:
    updates, values = [], []
    if fired:
        updates.append("fired=1")
    if failed:
        updates.append("delivery_failed=1")
    if attempted_at:
        updates.extend(["attempt_count=attempt_count+1", "last_attempt_at=?"])
        values.append(attempted_at.isoformat())
    if not updates:
        return
    with _LOCK, _managed_connection() as conn:
        conn.execute(
            f"UPDATE reminders SET {', '.join(updates)} WHERE id=?",
            (*values, str(reminder_id)),
        )


def _reminder_row(row) -> dict:
    return {
        "_id": row["id"],
        "internal_id": row["internal_id"],
        "platform": row["platform"],
        "message": row["message"],
        "due_at": datetime.fromisoformat(row["due_at"]),
        "created_at": datetime.fromisoformat(row["created_at"]),
        "fired": bool(row["fired"]),
        "attempt_count": row["attempt_count"],
    }
