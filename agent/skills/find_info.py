# agent/skills/find_info.py

import asyncio
import httpx
import os
from bs4 import BeautifulSoup
from llm import manager
from memory.scraper_patterns import ScraperPatternManager
from urllib.parse import urlparse
from datetime import datetime
import json
import logging
import ipaddress

logger = logging.getLogger(__name__)

def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

# Info search task-specific configuration from environment variables
INFO_SEARCH_TEMPERATURE = _get_float_env("INFO_SEARCH_TEMPERATURE", 0.2)
INFO_SEARCH_MAX_TOKENS = _get_int_env("INFO_SEARCH_MAX_TOKENS", 512)  # Reduced default to leave room for prompt
MAX_SOURCES = _get_int_env("INFO_SEARCH_MAX_SOURCES", 3)  # Limit number of sources to prevent context overflow
MAX_SNIPPET_CHARS = _get_int_env("INFO_SEARCH_MAX_SNIPPET_CHARS", 400)  # Max chars per snippet (conservative estimate: ~100 tokens)

async def is_safe_url(url: str) -> bool:
    """
    Validates URL to prevent SSRF attacks using async DNS resolution.
    
    Returns True if the URL is safe to fetch, False otherwise.
    Blocks:
    - Non-http/https schemes
    - localhost and loopback addresses
    - Private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Link-local addresses (169.254.0.0/16)
    - Cloud metadata endpoints (169.254.169.254)
    - Reserved/unspecified addresses
    - Multicast addresses
    - URLs exceeding maximum length
    - Suspicious ports
    """
    try:
        # Check URL length to prevent DoS
        MAX_URL_LENGTH = 2048
        if len(url) > MAX_URL_LENGTH:
            logger.warning(f"Blocked URL exceeding max length ({len(url)} > {MAX_URL_LENGTH}): {url[:100]}...")
            return False
        
        parsed = urlparse(url)
        
        # Only allow http and https schemes
        if parsed.scheme not in ('http', 'https'):
            logger.warning(f"Blocked URL with invalid scheme: {url}")
            return False
        
        # Get hostname
        hostname = parsed.hostname
        if not hostname:
            logger.warning(f"Blocked URL with no hostname: {url}")
            return False
        
        # Check hostname length
        if len(hostname) > 253:  # Max DNS hostname length
            logger.warning(f"Blocked URL with excessively long hostname: {url}")
            return False
        
        # Block localhost variations (pre-check before DNS resolution)
        # Note: DNS resolution below will catch additional loopback addresses
        if hostname.lower() in ('localhost', '0.0.0.0', '127.0.0.1', '::1', '::'):
            logger.warning(f"Blocked localhost URL: {url}")
            return False
        
        # Check for suspicious ports (optional but recommended)
        # Block common internal service ports to prevent port scanning
        BLOCKED_PORTS = {
            22,    # SSH
            23,    # Telnet
            25,    # SMTP
            135,   # Windows RPC
            139,   # NetBIOS
            445,   # SMB
            1433,  # MSSQL
            3306,  # MySQL
            3389,  # RDP
            5432,  # PostgreSQL
            5900,  # VNC
            6379,  # Redis
            8080,  # Common internal HTTP
            9200,  # Elasticsearch
            27017, # MongoDB
        }
        
        port = parsed.port
        if port and port in BLOCKED_PORTS:
            logger.warning(f"Blocked URL with suspicious port {port}: {url}")
            return False
        
        # Resolve hostname to IP address and validate ALL resolved IPs
        # If ANY resolved IP is unsafe, reject the URL (prevents DNS rebinding attacks)
        # Use asyncio.get_running_loop().getaddrinfo for non-blocking DNS resolution
        try:
            loop = asyncio.get_running_loop()
            # Use asyncio's getaddrinfo to perform async DNS resolution
            addr_info = await loop.getaddrinfo(hostname, None)
            for family, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                ip = ipaddress.ip_address(ip_str)
                
                # Block unspecified addresses (0.0.0.0, ::)
                if hasattr(ip, 'is_unspecified') and ip.is_unspecified:
                    logger.warning(f"Blocked unspecified address: {url} -> {ip}")
                    return False
                
                # Block loopback addresses
                if ip.is_loopback:
                    logger.warning(f"Blocked loopback address: {url} -> {ip}")
                    return False
                
                # Block private addresses
                if ip.is_private:
                    logger.warning(f"Blocked private address: {url} -> {ip}")
                    return False
                
                # Block link-local addresses (including 169.254.169.254)
                if ip.is_link_local:
                    logger.warning(f"Blocked link-local address: {url} -> {ip}")
                    return False
                
                # Block multicast addresses
                if ip.is_multicast:
                    logger.warning(f"Blocked multicast address: {url} -> {ip}")
                    return False
                
                # Block reserved addresses (future use, broadcast, etc.)
                if ip.is_reserved:
                    logger.warning(f"Blocked reserved address: {url} -> {ip}")
                    return False
        
        except (OSError, ValueError) as e:
            # DNS resolution failed or invalid IP
            # OSError covers socket.gaierror and socket.herror
            logger.warning(f"Could not resolve hostname for URL validation: {url} - {e}")
            return False
        
        return True
    
    except Exception as e:
        logger.error(f"Error validating URL {url}: {e}")
        return False

async def search_sources_llm(query):
    prompt = (
        f"Suggest 3 to 5 reputable web sources (with full URLs) where I can find up-to-date information for the following request:\n"
        f"Request: {query}\n"
        "Just output the URLs, one per line."
    )
    response = await asyncio.to_thread(manager.ask_llm, prompt, temperature=INFO_SEARCH_TEMPERATURE, max_tokens=INFO_SEARCH_MAX_TOKENS)
    urls = [line.strip() for line in response.splitlines() if line.strip().startswith("http")]
    # Filter URLs to only include safe ones (SSRF protection)
    # Batch validation using asyncio.gather for efficient async DNS resolution
    validation_results = await asyncio.gather(*[is_safe_url(url) for url in urls]) if urls else []
    safe_urls = [url for url, is_safe in zip(urls, validation_results) if is_safe]
    if len(safe_urls) < len(urls):
        logger.warning(f"Filtered out {len(urls) - len(safe_urls)} unsafe URLs from LLM response")
    return safe_urls

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

async def scrape_url(url, pattern=None):
    # Validate URL to prevent SSRF attacks
    if not await is_safe_url(url):
        logger.error(f"Blocked unsafe URL in scrape_url: {url}")
        return f"Error scraping {url}: URL blocked for security reasons"
    
    try:
        # Disable automatic redirects and handle them manually with validation
        # This prevents redirect-based SSRF attacks
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, max_redirects=0) as client:
            resp = await client.get(url)
            
            # Handle redirects manually with security validation
            redirect_count = 0
            MAX_REDIRECTS = 5
            
            while resp.status_code in (301, 302, 303, 307, 308) and redirect_count < MAX_REDIRECTS:
                redirect_url = resp.headers.get('Location')
                if not redirect_url:
                    break
                
                # Make redirect URL absolute if it's relative
                if not redirect_url.startswith('http'):
                    from urllib.parse import urljoin
                    redirect_url = urljoin(url, redirect_url)
                
                # Validate redirect target
                if not await is_safe_url(redirect_url):
                    logger.error(f"Blocked unsafe redirect from {url} to {redirect_url}")
                    return f"Error scraping {url}: Redirect to unsafe location blocked"
                
                logger.info(f"Following redirect from {url} to {redirect_url}")
                url = redirect_url
                resp = await client.get(url)
                redirect_count += 1
            
            if redirect_count >= MAX_REDIRECTS:
                logger.warning(f"Too many redirects for {url}")
                return f"Error scraping {url}: Too many redirects"
            
            resp.raise_for_status()
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
            except Exception as e:
                # If pattern-based extraction fails, fall back to full-page text below.
                # This exception is non-fatal and is logged for debugging purposes.
                logger.warning(f"Pattern-based scraping failed for {url}: {e}", exc_info=True)
        text = soup.get_text(separator="\n", strip=True)
        return text[:MAX_SNIPPET_CHARS]
    except Exception as e:
        return f"Error scraping {url}: {e}"

async def cross_reference_llm(query, snippets):
    """
    Cross-references multiple source snippets to answer a query.
    Implements token-aware truncation to prevent exceeding LLM context window.
    """
    # Limit number of sources to prevent context overflow
    limited_snippets = snippets[:MAX_SOURCES]
    
    # Truncate each snippet to max chars (accounting for truncation suffix)
    truncation_suffix = "... [truncated]"
    truncated_snippets = [
        snippet[:MAX_SNIPPET_CHARS - len(truncation_suffix)] + truncation_suffix
        if len(snippet) > MAX_SNIPPET_CHARS
        else snippet
        for snippet in limited_snippets
    ]
    
    joined = "\n---\n".join(truncated_snippets)
    prompt = (
        f"Given the following user request:\n{query}\n"
        "Here are snippets from multiple sources:\n"
        f"{joined}\n"
        "Based on these, answer the user's question in a concise, up-to-date summary. If information conflicts, mention the discrepancy."
    )
    
    # Early validation: check if prompt is reasonable
    # Conservative estimate: 4 chars per token (varies by tokenizer and language)
    # This is an approximation; actual token count may differ depending on the LLM's tokenizer
    estimated_prompt_tokens = len(prompt) / 4
    if estimated_prompt_tokens > (manager.MODEL_CONTEXT_SIZE - INFO_SEARCH_MAX_TOKENS - manager.CONTEXT_BUFFER_TOKENS):
        logger.warning(f"Prompt estimated at {estimated_prompt_tokens} tokens, may exceed context window")
    
    return await asyncio.to_thread(manager.ask_llm, prompt, temperature=INFO_SEARCH_TEMPERATURE, max_tokens=INFO_SEARCH_MAX_TOKENS)


class DynamicScraper:
    """
    Minimal dynamic scraper that delegates source discovery to the LLM-based
    `search_sources_llm` helper defined in this module.
    """

    async def find_sources(self, query: str):
        return await search_sources_llm(query)


class AdaptiveScraper:
    """
    Minimal adaptive scraper that delegates scraping and pattern persistence
    to helpers defined in this module.
    """

    async def analyze_webpage(self, url: str, query: str):
        # Try to load an existing scraper pattern for this URL, if any.
        pattern = await asyncio.to_thread(load_scraper_pattern, url)
        return await scrape_url(url, pattern=pattern)

    async def save_scraper_pattern(
        self,
        url,
        domain,
        query_type,
        content_pattern,
        success: bool = True,
        error_msg: str | None = None,
    ):
        return await asyncio.to_thread(
            save_scraper_pattern,
            url=url,
            domain=domain,
            query_type=query_type,
            content_pattern=content_pattern,
            success=success,
            error_msg=error_msg,
        )
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
            # Ensure data is a string to avoid TypeError when checking for "Error scraping"
            if data is None:
                data = f"Error scraping {url}: no data returned"
            elif not isinstance(data, str):
                data = f"Error scraping {url}: unexpected response type {type(data).__name__}"
            if "Error scraping" not in data:
                # Save the pattern (for demo, use main_selector=body or enhance with LLM)
                example_pattern = {"main_selector": "body"}
                await adaptive.save_scraper_pattern(
                    url, domain, query_type=query, content_pattern=example_pattern, success=True
                )
            else:
                await adaptive.save_scraper_pattern(
                    url, domain, query_type=query, content_pattern=None, success=False, error_msg=data
                )
            return data
        except Exception as e:
            await adaptive.save_scraper_pattern(
                url, domain, query_type=query, content_pattern=None, success=False, error_msg=str(e)
            )
            return f"Error scraping {url}: {e}"

    results = await asyncio.gather(*[scrape_and_learn(url) for url in urls])

    # 3. Cross-reference results and answer
    return await cross_reference_llm(query, results)