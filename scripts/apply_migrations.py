import os
import psycopg2

PG_CONN_INFO = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB", "assistant_db"),
    "user": os.getenv("POSTGRES_USER", "assistant"),
    "password": os.getenv("POSTGRES_PASSWORD", "assistantpass"),
}


def apply_migrations(migrations_dir="./migrations"):
    files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".up.sql")])
    with psycopg2.connect(**PG_CONN_INFO) as conn:
        cur = conn.cursor()
        for filename in files:
            path = os.path.join(migrations_dir, filename)
            print(f"Applying {filename} ...")
            with open(path, "r") as f:
                sql = f.read()
                cur.execute(sql)
        conn.commit()
    print("All migrations applied.")


if __name__ == "__main__":
    apply_migrations()
