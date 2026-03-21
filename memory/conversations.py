# memory/conversations.py

from datetime import datetime
from .database import get_pg_conn


class ConversationManager:
    @staticmethod
    def save_conversation(user_internal_id, role, message):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO conversation_memory (user_internal_id, timestamp, role, message) VALUES (%s, %s, %s, %s)",
                (str(user_internal_id), datetime.utcnow(), role, message),
            )
            conn.commit()

    @staticmethod
    def load_recent_conversation(user_internal_id, limit=10):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT role, message FROM conversation_memory WHERE user_internal_id = %s ORDER BY timestamp DESC LIMIT %s",
                (str(user_internal_id), limit),
            )
            results = cur.fetchall()
            return list(reversed([(row["role"], row["message"]) for row in results]))

    @staticmethod
    def clear_conversation(user_internal_id=None):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            if user_internal_id:
                cur.execute(
                    "DELETE FROM conversation_memory WHERE user_internal_id = %s",
                    (str(user_internal_id),),
                )
            else:
                cur.execute("DELETE FROM conversation_memory")
            conn.commit()

    @staticmethod
    def get_conversation_count(user_internal_id: str) -> int:
        """Return the total number of stored messages for this user."""
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) as cnt FROM conversation_memory WHERE user_internal_id = %s",
                (str(user_internal_id),),
            )
            row = cur.fetchone()
            return row["cnt"] if row else 0
