import uuid

def get_or_create_user_id(channel, external_id):
    with get_pg_conn() as conn:
        cur = conn.cursor()
        field = f"{channel}_id"
        # Platform ID columns are TEXT[]; use ANY() for the membership lookup
        cur.execute(f"SELECT internal_id FROM users WHERE %s = ANY({field})", (str(external_id),))
        row = cur.fetchone()
        if row:
            return row[0]
        # Create new user — wrap the id in an ARRAY literal for the TEXT[] column
        new_uuid = str(uuid.uuid4())
        cur.execute(
            f"INSERT INTO users (internal_id, {field}) VALUES (%s, ARRAY[%s]::TEXT[])",
            (new_uuid, str(external_id)),
        )
        conn.commit()
        return new_uuid