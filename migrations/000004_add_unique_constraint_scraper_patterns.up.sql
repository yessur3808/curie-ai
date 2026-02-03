-- Add unique constraint on url and query_type to prevent duplicate patterns
-- This ensures one pattern per (url, query_type) combination
ALTER TABLE scraper_patterns 
ADD CONSTRAINT unique_url_query_type UNIQUE (url, query_type);
