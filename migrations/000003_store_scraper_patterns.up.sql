CREATE TABLE scraper_patterns (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    query_type TEXT,
    content_pattern JSONB,     -- Stores selectors, structure, etc.
    last_success TIMESTAMP,
    last_error TEXT,
    reliability_score FLOAT DEFAULT 0.5,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_scraper_patterns_domain ON scraper_patterns(domain);
CREATE INDEX idx_scraper_patterns_url ON scraper_patterns(url);