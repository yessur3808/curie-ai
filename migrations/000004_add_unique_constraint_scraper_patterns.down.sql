-- Remove unique constraint on url and query_type
ALTER TABLE scraper_patterns 
DROP CONSTRAINT IF EXISTS unique_url_query_type;
