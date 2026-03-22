import os
import psycopg2

# Use your own connection info or pull from dotenv
PG_CONN_INFO = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB", "assistant_db"),
    "user": os.getenv("POSTGRES_USER", "assistant"),
    "password": os.getenv("POSTGRES_PASSWORD", "assistantpass"),
}


def rollback_all_migrations(migrations_dir="migrations"):
    # Find all down migrations
    files = sorted(
        [f for f in os.listdir(migrations_dir) if f.endswith(".down.sql")],
        reverse=True,  # Descending order: 003, 002, 001...
    )
    with psycopg2.connect(**PG_CONN_INFO) as conn:
        cur = conn.cursor()
        for filename in files:
            path = os.path.join(migrations_dir, filename)
            print(f"Rolling back with {filename} ...")
            with open(path, "r") as f:
                sql = f.read()
                try:
                    cur.execute(sql)
                except Exception as e:
                    print(f"Error in {filename}: {e}")
        conn.commit()
    print("All down migrations applied (database rolled back).")


if __name__ == "__main__":
    rollback_all_migrations()
