# memory/scraper_patterns.py

from datetime import datetime
from .database import get_pg_conn

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
                VALUES (%s, %s, %s, %s::JSONB, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                url, domain, query_type, content_pattern,
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
            """, (url,))
            results = cur.fetchall()
            return [dict(zip([desc[0] for desc in cur.description], row)) for row in results]

    @staticmethod
    def update_pattern(id, **fields):
        if not fields:
            return False
        # Allowlist of columns that can be updated to prevent SQL injection
        allowed_columns = {
            'url', 'domain', 'query_type', 'content_pattern',
            'last_success', 'last_error', 'reliability_score'
        }
        # Filter fields to only include allowed columns
        filtered_fields = {k: v for k, v in fields.items() if k in allowed_columns}
        if not filtered_fields:
            return False
        
        set_clause = ", ".join([f"{k} = %s" for k in filtered_fields.keys()])
        values = list(filtered_fields.values())
        # Add updated_at and id to the values list
        values.extend([datetime.utcnow(), id])
        
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                UPDATE scraper_patterns SET {set_clause}, updated_at = %s WHERE id = %s
            """, values)
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