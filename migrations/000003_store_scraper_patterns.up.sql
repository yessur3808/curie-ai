CREATE TABLE IF NOT EXISTS scraper_patterns (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    query_type TEXT,
    content_pattern JSONB,     -- Stores selectors, structure, etc.
    last_success TIMESTAMPTZ,
    last_error TEXT,
    reliability_score FLOAT DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scraper_patterns_domain ON scraper_patterns(domain);
CREATE INDEX IF NOT EXISTS idx_scraper_patterns_url ON scraper_patterns(url);