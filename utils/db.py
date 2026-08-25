# utils/db.py


import os


def is_master_user(internal_id):
    configured = os.getenv("MASTER_USER_ID", "").strip()
    if configured:
        return configured == str(internal_id)
    from memory.database import get_pg_conn

    with get_pg_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT is_master FROM users WHERE internal_id = %s", (str(internal_id),)
        )
        row = cur.fetchone()
        return bool(row["is_master"]) if row else False


def make_user_master(internal_id):
    from memory.database import get_pg_conn

    with get_pg_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET is_master = TRUE WHERE internal_id = %s",
            (str(internal_id),),
        )
        conn.commit()
