import uuid
import psycopg2
import os
import argparse
from contextlib import contextmanager
from psycopg2.extras import DictCursor
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

PG_CONN_INFO = {
    "host": os.getenv("POSTGRES_HOST"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB"),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


@contextmanager
def get_pg_conn():
    conn = psycopg2.connect(**PG_CONN_INFO, cursor_factory=DictCursor)
    try:
        yield conn
    finally:
        conn.close()


# Allowlist of valid channel names — these map to column identifiers in SQL so
# we must validate before interpolating to prevent SQL injection.
_ALLOWED_CHANNELS = frozenset(
    ["telegram", "slack", "whatsapp", "signal", "discord", "api"]
)


def _validate_channel(channel: str) -> None:
    if channel not in _ALLOWED_CHANNELS:
        raise ValueError(
            f"Unknown channel {channel!r}. Must be one of: {sorted(_ALLOWED_CHANNELS)}"
        )


def get_or_create_user_id(
    channel,
    external_id,
    name=None,
    email=None,
    is_master=False,
    internal_id=None,
    secret_username=None,
):
    if not secret_username:
        raise ValueError("secret_username is required!")
    _validate_channel(channel)

    with get_pg_conn() as conn:
        cur = conn.cursor()
        field = f"{channel}_id"

        # Platform ID columns are TEXT[]; use ANY() for the lookup
        cur.execute(
            f"SELECT internal_id FROM users WHERE %s = ANY({field})",
            (str(external_id),),
        )
        row = cur.fetchone()
        if row:
            # Update the updated_at timestamp and updated_by
            cur.execute(
                """
                UPDATE users 
                SET updated_at = CURRENT_TIMESTAMP, 
                    updated_by = 'master'
                WHERE internal_id = %s
                RETURNING internal_id
            """,
                (str(row["internal_id"]),),
            )
            conn.commit()
            updated_row = cur.fetchone()
            return str(updated_row["internal_id"])

        # Use provided internal_id or generate new one
        new_uuid = internal_id if internal_id else str(uuid.uuid4())

        # Build insert statement with all required fields
        fields = [
            "internal_id",
            field,
            "is_master",
            "secret_username",
            "roles",
            "updated_by",
            "updated_at",
        ]
        # Platform ID column is TEXT[]; wrap the value in an ARRAY literal
        values = [
            new_uuid,
            str(external_id),
            is_master,
            secret_username,
            ["master"] if is_master else [],
            "master",
        ]
        # Placeholders: ARRAY[%s]::TEXT[] for the id column, %s for the rest,
        # CURRENT_TIMESTAMP for updated_at
        placeholders = ["%s", "ARRAY[%s]::TEXT[]"] + ["%s"] * 4 + ["CURRENT_TIMESTAMP"]

        # Add optional fields if provided
        if email:
            fields.append("email")
            values.append(email)
            placeholders.append("ARRAY[%s]::TEXT[]")

        sql = f"""
            INSERT INTO users ({', '.join(fields)}) 
            VALUES ({', '.join(placeholders)})
            RETURNING internal_id
        """
        cur.execute(sql, tuple(values))
        conn.commit()
        new_row = cur.fetchone()
        return str(new_row["internal_id"])


def delete_master_user():
    master_internal_id = os.getenv("MASTER_USER_ID")

    if not master_internal_id:
        raise RuntimeError("MASTER_USER_ID must be set in your .env file!")

    with get_pg_conn() as conn:
        cur = conn.cursor()
        try:
            # First, check if the master user exists
            cur.execute(
                """
                SELECT internal_id 
                FROM users 
                WHERE internal_id = %s AND is_master = TRUE
            """,
                (master_internal_id,),
            )
            user = cur.fetchone()

            if not user:
                print("⚠️  Master user not found in database!")
                return False

            # Update the record first with final update timestamp
            cur.execute(
                """
                UPDATE users 
                SET updated_at = CURRENT_TIMESTAMP,
                    updated_by = 'master'
                WHERE internal_id = %s AND is_master = TRUE
            """,
                (master_internal_id,),
            )

            # Then delete the master user
            cur.execute(
                """
                DELETE FROM users 
                WHERE internal_id = %s AND is_master = TRUE
            """,
                (master_internal_id,),
            )
            conn.commit()

            if cur.rowcount > 0:
                print(
                    f"🗑️  Successfully deleted master user with internal_id: {master_internal_id}"
                )
                return True
            else:
                print("⚠️  Failed to delete master user!")
                return False

        except Exception as e:
            print(f"❌ Error deleting master user: {str(e)}")
            conn.rollback()
            return False


def ensure_master_user():
    master_channel = "telegram"
    master_external_id = os.getenv("MASTER_TELEGRAM_ID")
    master_name = os.getenv("MASTER_USER_NAME")
    master_email = os.getenv("MASTER_EMAIL", "")
    master_internal_id = os.getenv("MASTER_USER_ID")
    master_secret_username = os.getenv("MASTER_SECRET_USERNAME")

    if not master_internal_id:
        raise RuntimeError("MASTER_USER_ID must be set in your .env file!")

    if not master_external_id:
        raise RuntimeError("MASTER_TELEGRAM_ID must be set in your .env file!")

    if not master_secret_username:
        raise RuntimeError("MASTER_SECRET_USERNAME must be set in your .env file!")

    print(f"👤 Ensuring master user: {master_secret_username}")

    return get_or_create_user_id(
        channel=master_channel,
        external_id=master_external_id,
        name=master_name,
        email=master_email,
        is_master=True,
        internal_id=master_internal_id,
        secret_username=master_secret_username,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage master user in the database")
    parser.add_argument(
        "-d", "--delete", action="store_true", help="Delete the master user"
    )
    args = parser.parse_args()

    if args.delete:
        if delete_master_user():
            print("✅ Master user deletion completed!")
        else:
            print("❌ Master user deletion failed!")
    else:
        master_id = ensure_master_user()
        print(f"✅ Master user ensured in database with internal_id: {master_id}")
