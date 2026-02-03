# memory/scraper_patterns.py

from datetime import datetime
from psycopg2.extras import Json
from psycopg2 import sql
from .database import get_pg_conn

# Allowlist of columns that can be updated to prevent SQL injection
ALLOWED_UPDATE_COLUMNS = {
    'url', 'domain', 'query_type', 'content_pattern',
    'last_success', 'last_error', 'reliability_score'
}

class ScraperPatternManager:
    @staticmethod
    def save_pattern(url, domain, query_type=None, content_pattern=None,
                     last_success=None, last_error=None, reliability_score=0.5,
                     created_at=None, updated_at=None):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            now = datetime.utcnow()
            created_at = created_at or now
            updated_at = updated_at or now
            cur.execute("""
                INSERT INTO scraper_patterns
                (url, domain, query_type, content_pattern, last_success, last_error,
                 reliability_score, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                url, domain, query_type, Json(content_pattern) if content_pattern is not None else None,
                last_success, last_error, reliability_score, created_at, updated_at
            ))
            conn.commit()
            return cur.fetchone()[0]

    @staticmethod
    def load_by_domain(domain, limit=10):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM scraper_patterns WHERE domain = %s
                ORDER BY reliability_score DESC, updated_at DESC
                LIMIT %s
            """, (domain, limit))
            results = cur.fetchall()
            return [dict(zip([desc[0] for desc in cur.description], row)) for row in results]

    @staticmethod
    def load_by_url(url):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM scraper_patterns WHERE url = %s
                ORDER BY reliability_score DESC, updated_at DESC
                LIMIT 1
            """, (url,))
            results = cur.fetchall()
            return [dict(zip([desc[0] for desc in cur.description], row)) for row in results]

    @staticmethod
    def update_pattern(id, **fields):
        if not fields:
            return False
        # Filter fields to only allowed columns
        allowed_fields = {k: v for k, v in fields.items() if k in ALLOWED_UPDATE_COLUMNS}
        if not allowed_fields:
            return False
        # Wrap content_pattern with Json() if present
        if 'content_pattern' in allowed_fields and allowed_fields['content_pattern'] is not None:
            allowed_fields['content_pattern'] = Json(allowed_fields['content_pattern'])
        # Use psycopg2.sql.Identifier for column names to prevent SQL injection
        set_clause = sql.SQL(", ").join([
            sql.SQL("{} = %s").format(sql.Identifier(k))
            for k in allowed_fields.keys()
        ])
        values = list(allowed_fields.values())
        values.append(datetime.utcnow())
        values.append(id)
        with get_pg_conn() as conn:
            cur = conn.cursor()
            query = sql.SQL("""
                UPDATE scraper_patterns SET {}, updated_at = %s WHERE id = %s
            """).format(set_clause)
            cur.execute(query, values)
            conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def delete_pattern(id):
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM scraper_patterns WHERE id = %s", (id,))
            conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def create_indexes():
        with get_pg_conn() as conn:
            cur = conn.cursor()
            # These are idempotent, so it's safe to call at startup
            cur.execute("CREATE INDEX IF NOT EXISTS idx_scraper_patterns_domain ON scraper_patterns(domain);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_scraper_patterns_url ON scraper_patterns(url);")
            conn.commit()