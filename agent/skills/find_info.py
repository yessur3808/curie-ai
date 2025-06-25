# agent/skills/find_info.py

import asyncio
import httpx
from bs4 import BeautifulSoup
from llm import manager
from memory.scraper_patterns import ScraperPatternManager
from urllib.parse import urlparse
from datetime import datetime
import json

async def search_sources_llm(query):
    prompt = (
        f"Suggest 3 to 5 reputable web sources (with full URLs) where I can find up-to-date information for the following request:\n"
        f"Request: {query}\n"
        "Just output the URLs, one per line."
    )
    response = manager.ask_llm(prompt, temperature=0.2, max_tokens=256)
    urls = [line.strip() for line in response.splitlines() if line.strip().startswith("http")]
    return urls

def load_scraper_pattern(url):
    patterns = ScraperPatternManager.load_by_url(url)
    if patterns:
        return patterns[0].get('content_pattern')
    return None

def save_scraper_pattern(url, domain, query_type, content_pattern, success=True, error_msg=None):
    ScraperPatternManager.save_pattern(
        url=url,
        domain=domain,
        query_type=query_type,
        content_pattern=content_pattern,
        last_success=datetime.utcnow() if success else None,
        last_error=error_msg if not success else None,
        reliability_score=0.9 if success else 0.2,
        updated_at=datetime.utcnow()
    )

async def scrape_url(url, query, pattern=None):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")

        if pattern:
            try:
                pat = pattern if isinstance(pattern, dict) else json.loads(pattern)
                main_selector = pat.get("main_selector")
                if main_selector:
                    node = soup.select_one(main_selector)
                    if node:
                        return node.get_text(separator=" ", strip=True)
            except Exception:
                pass
        text = soup.get_text(separator="\n", strip=True)
        return text[:2000]
    except Exception as e:
        return f"Error scraping {url}: {e}"

async def cross_reference_llm(query, snippets):
    joined = "\n---\n".join(snippets)
    prompt = (
        f"Given the following user request:\n{query}\n"
        "Here are snippets from multiple sources:\n"
        f"{joined}\n"
        "Based on these, answer the user's question in a concise, up-to-date summary. If information conflicts, mention the discrepancy."
    )
    return manager.ask_llm(prompt, temperature=0.2, max_tokens=256)


async def find_info(query):
    scraper = DynamicScraper()
    adaptive = AdaptiveScraper()

    # 1. Discover sources using DynamicScraper
    urls = await scraper.find_sources(query)
    if not urls:
        return "Sorry, I couldn't find any sources for that."

    # 2. Scrape with AdaptiveScraper (using pattern learning)
    async def scrape_and_learn(url):
        domain = urlparse(url).netloc
        try:
            data = await adaptive.analyze_webpage(url, query)
            if "Error scraping" not in data:
                # Save the pattern (for demo, use main_selector=body or enhance with LLM)
                example_pattern = '{"main_selector": "body"}'
                adaptive.save_scraper_pattern(
                    url, domain, query_type=query, content_pattern=example_pattern, success=True
                )
            else:
                adaptive.save_scraper_pattern(
                    url, domain, query_type=query, content_pattern=None, success=False, error_msg=data
                )
            return data
        except Exception as e:
            adaptive.save_scraper_pattern(
                url, domain, query_type=query, content_pattern=None, success=False, error_msg=str(e)
            )
            return f"Error scraping {url}: {e}"

    results = await asyncio.gather(*[scrape_and_learn(url) for url in urls])

    # 3. Cross-reference results and answer
    return await cross_reference_llm(query, results)