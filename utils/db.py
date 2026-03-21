# utils/db.py


def is_master_user(internal_id):
    with get_pg_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT is_master FROM users WHERE internal_id = %s", (str(internal_id),)
        )
        row = cur.fetchone()
        return bool(row["is_master"]) if row else False


def make_user_master(internal_id):
    with get_pg_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET is_master = TRUE WHERE internal_id = %s",
            (str(internal_id),),
        )
        conn.commit()
