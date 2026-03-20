# memory/users.py

import uuid
from datetime import datetime
from .database import get_pg_conn, mongo_db

# Allowlist of valid channel/platform names.
# Channel names are interpolated into SQL column identifiers, so we must
# validate them against a fixed set to prevent SQL injection.
_ALLOWED_CHANNELS = frozenset(["telegram", "slack", "whatsapp", "signal", "discord", "api"])


def _validate_channel(channel: str) -> None:
    if channel not in _ALLOWED_CHANNELS:
        raise ValueError(
            f"Unknown channel {channel!r}. Must be one of: {sorted(_ALLOWED_CHANNELS)}"
        )


class UserManager:
    @staticmethod
    def get_internal_id_by_secret_username(secret_username):
        """Get the internal_id (UUID) for a user by their secret_username."""
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT internal_id FROM users WHERE secret_username ILIKE %s", (secret_username,))
            row = cur.fetchone()
            return str(row['internal_id']) if row else None

    @staticmethod
    def get_or_create_user_internal_id(channel, external_id, secret_username=None, updated_by=None, is_master=False, roles=None):
        """
        Look up or create a user based on external channel ID.

        The platform ID columns are TEXT[], so a single user can have multiple
        IDs on the same platform.  Lookups use ``= ANY(field)`` and inserts
        wrap the value in ``ARRAY[...]::TEXT[]``.

        If creating, must supply secret_username and updated_by.
        """
        _validate_channel(channel)
        with get_pg_conn() as conn:
            cur = conn.cursor()
            field = f"{channel}_id"
            # TEXT[] column — use ANY() for membership lookup
            cur.execute(
                f"SELECT internal_id FROM users WHERE %s = ANY({field})",
                (str(external_id),),
            )
            row = cur.fetchone()
            if row:
                return str(row['internal_id'])
            # Create new user
            if not secret_username or not updated_by:
                raise ValueError("secret_username and updated_by are required to create a new user.")
            new_uuid = str(uuid.uuid4())
            cur.execute(
                f"""INSERT INTO users (internal_id, {field}, secret_username, updated_by, is_master, roles)
                    VALUES (%s, ARRAY[%s]::TEXT[], %s, %s, %s, %s)
                    RETURNING internal_id""",
                (
                    new_uuid,
                    str(external_id),
                    secret_username,
                    updated_by,
                    is_master,
                    roles if roles else []
                )
            )
            conn.commit()

            # Initialize user profile in MongoDB with proactive messaging enabled by default
            # Master users and all new users get proactive messaging enabled
            default_profile = {
                "proactive_messaging_enabled": True,
                "proactive_interval_hours": 24
            }
            mongo_db.user_profiles.update_one(
                {"_id": new_uuid},
                {
                    "$set": {"facts": default_profile},
                    "$currentDate": {"last_updated": True}
                },
                upsert=True
            )

            return new_uuid

    @staticmethod
    def add_platform_id(internal_id: str, channel: str, external_id: str) -> None:
        """Append *external_id* to the TEXT[] id column for *channel*.

        Idempotent — if the ID is already present in the array the column is
        left unchanged.  If the column is currently NULL it is initialised to a
        single-element array.
        """
        _validate_channel(channel)
        field = f"{channel}_id"
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""UPDATE users
                    SET {field} = CASE
                        WHEN {field} IS NULL         THEN ARRAY[%s]::TEXT[]
                        WHEN %s = ANY({field})        THEN {field}
                        ELSE array_append({field}, %s)
                    END,
                    updated_at = %s
                    WHERE internal_id = %s""",
                (
                    str(external_id),
                    str(external_id),
                    str(external_id),
                    datetime.utcnow(),
                    str(internal_id),
                )
            )
            conn.commit()

    @staticmethod
    def add_email(internal_id: str, email: str) -> None:
        """Append *email* to the ``email TEXT[]`` column for this user.

        Idempotent — if the address is already present the column is left
        unchanged.  If the column is currently NULL it is initialised to a
        single-element array.
        """
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE users
                   SET email = CASE
                       WHEN email IS NULL       THEN ARRAY[%s]::TEXT[]
                       WHEN %s = ANY(email)     THEN email
                       ELSE array_append(email, %s)
                   END,
                   updated_at = %s
                   WHERE internal_id = %s""",
                (
                    str(email),
                    str(email),
                    str(email),
                    datetime.utcnow(),
                    str(internal_id),
                )
            )
            conn.commit()

    @staticmethod
    def get_user_profile(internal_id):
        """Returns the 'facts' dict for this user, or an empty dict if not found."""
        doc = mongo_db.user_profiles.find_one({"_id": str(internal_id)})
        return doc.get("facts", {}) if doc and "facts" in doc else {}

    @staticmethod
    def update_user_profile(internal_id, new_facts: dict):
        """
        Adds/updates facts for the user in MongoDB.
        Merges with any existing facts.
        """
        if not isinstance(new_facts, dict):
            raise ValueError("new_facts must be a dict")
        update = {}
        for k, v in new_facts.items():
            update[f"facts.{k}"] = v
        mongo_db.user_profiles.update_one(
            {"_id": str(internal_id)},
            {
                "$set": update,
                "$currentDate": {"last_updated": True}
            },
            upsert=True
        )

    @staticmethod
    def set_user_roles(internal_id, roles, updated_by):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET roles = %s, updated_at = %s, updated_by = %s WHERE internal_id = %s",
                (roles, datetime.utcnow(), updated_by, str(internal_id))
            )
            conn.commit()

    @staticmethod
    def set_user_master(internal_id, is_master=True, updated_by=None):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET is_master = %s, updated_at = %s, updated_by = %s WHERE internal_id = %s",
                (is_master, datetime.utcnow(), updated_by, str(internal_id))
            )
            conn.commit()

    @staticmethod
    def get_user_by_internal_id(internal_id):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE internal_id = %s", (str(internal_id),))
            return cur.fetchone()