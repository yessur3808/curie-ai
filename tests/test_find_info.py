# tests/test_find_info.py

import sys
import os
import asyncio
from unittest.mock import AsyncMock, patch, Mock

# Add parent directory to path to import agent modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.skills.find_info import (
    search_sources_llm,
    scrape_url,
    cross_reference_llm,
    DynamicScraper,
    AdaptiveScraper,
    find_info,
)


# Test search_sources_llm
async def test_search_sources_llm_returns_safe_urls():
    """Test that search_sources_llm filters out unsafe URLs"""
    with patch('agent.skills.find_info.manager.ask_llm') as mock_ask_llm, \
         patch('agent.skills.find_info.is_safe_url') as mock_is_safe:
        # Mock LLM response with mix of safe and unsafe URLs
        mock_ask_llm.return_value = (
            "http://example.com\n"
            "http://localhost:8080\n"
            "https://wikipedia.org\n"
            "http://127.0.0.1\n"
        )
        
        # Mock is_safe_url to return True for safe, False for unsafe
        def is_safe_side_effect(url):
            return url not in ["http://localhost:8080", "http://127.0.0.1"]
        
        mock_is_safe.side_effect = is_safe_side_effect
        
        result = await search_sources_llm("test query")
        
        assert len(result) == 2
        assert "http://example.com" in result
        assert "https://wikipedia.org" in result
        assert "http://localhost:8080" not in result
        assert "http://127.0.0.1" not in result


async def test_search_sources_llm_with_no_urls():
    """Test search_sources_llm when LLM returns no URLs"""
    with patch('agent.skills.find_info.manager.ask_llm') as mock_ask_llm:
        mock_ask_llm.return_value = "Sorry, I couldn't find any sources."
        
        result = await search_sources_llm("test query")
        
        assert result == []


async def test_search_sources_llm_with_all_unsafe_urls():
    """Test search_sources_llm when all URLs are unsafe"""
    with patch('agent.skills.find_info.manager.ask_llm') as mock_ask_llm, \
         patch('agent.skills.find_info.is_safe_url') as mock_is_safe:
        mock_ask_llm.return_value = (
            "http://localhost:8080\n"
            "http://127.0.0.1\n"
        )
        mock_is_safe.return_value = False
        
        result = await search_sources_llm("test query")
        
        assert result == []


# Test scrape_url
async def test_scrape_url_blocks_unsafe_url():
    """Test that scrape_url blocks unsafe URLs"""
    with patch('agent.skills.find_info.is_safe_url') as mock_is_safe:
        mock_is_safe.return_value = False
        
        result = await scrape_url("http://localhost:8080")
        
        assert "Error scraping" in result
        assert "blocked for security reasons" in result


async def test_scrape_url_success():
    """Test successful URL scraping"""
    with patch('agent.skills.find_info.is_safe_url') as mock_is_safe, \
         patch('agent.skills.find_info.httpx.AsyncClient') as mock_client:
        mock_is_safe.return_value = True
        
        # Mock HTTP response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Test content</p></body></html>"
        mock_response.raise_for_status = Mock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.get.return_value = mock_response
        mock_client.return_value = mock_client_instance
        
        result = await scrape_url("https://example.com")
        
        assert "Test content" in result
        assert "Error scraping" not in result


async def test_scrape_url_with_redirect():
    """Test scraping URL with redirect validation"""
    with patch('agent.skills.find_info.is_safe_url') as mock_is_safe, \
         patch('agent.skills.find_info.httpx.AsyncClient') as mock_client:
        # Mock is_safe_url to return True for initial URL and redirect
        async def is_safe_side_effect(url):
            return url in ["https://example.com", "https://example.com/new"]
        
        mock_is_safe.side_effect = is_safe_side_effect
        
        # Mock redirect response then final response
        mock_redirect_response = AsyncMock()
        mock_redirect_response.status_code = 302
        mock_redirect_response.headers = {"Location": "https://example.com/new"}
        mock_redirect_response.raise_for_status = Mock()
        
        mock_final_response = AsyncMock()
        mock_final_response.status_code = 200
        mock_final_response.text = "<html><body>Redirected content</body></html>"
        mock_final_response.raise_for_status = Mock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.get.side_effect = [mock_redirect_response, mock_final_response]
        mock_client.return_value = mock_client_instance
        
        result = await scrape_url("https://example.com")
        
        assert "Redirected content" in result
        assert mock_client_instance.get.call_count == 2


async def test_scrape_url_blocks_unsafe_redirect():
    """Test that scrape_url blocks redirects to unsafe URLs"""
    with patch('agent.skills.find_info.is_safe_url') as mock_is_safe, \
         patch('agent.skills.find_info.httpx.AsyncClient') as mock_client:
        # First URL is safe, redirect target is unsafe
        call_count = [0]
        async def is_safe_side_effect(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return True  # Initial URL is safe
            return False  # Redirect target is unsafe
        
        mock_is_safe.side_effect = is_safe_side_effect
        
        # Mock redirect response
        mock_redirect_response = AsyncMock()
        mock_redirect_response.status_code = 302
        mock_redirect_response.headers = {"Location": "http://localhost:8080"}
        mock_redirect_response.raise_for_status = Mock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.get.return_value = mock_redirect_response
        mock_client.return_value = mock_client_instance
        
        result = await scrape_url("https://example.com")
        
        assert "Error scraping" in result
        assert "unsafe location blocked" in result


async def test_scrape_url_with_pattern():
    """Test scraping with a specific pattern"""
    with patch('agent.skills.find_info.is_safe_url') as mock_is_safe, \
         patch('agent.skills.find_info.httpx.AsyncClient') as mock_client:
        mock_is_safe.return_value = True
        
        # Mock HTTP response with specific content structure
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = """
        <html>
            <body>
                <div class="main-content">Target content here</div>
                <div class="sidebar">Ignore this</div>
            </body>
        </html>
        """
        mock_response.raise_for_status = Mock()
        
        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None
        mock_client_instance.get.return_value = mock_response
        mock_client.return_value = mock_client_instance
        
        pattern = {"main_selector": ".main-content"}
        result = await scrape_url("https://example.com", pattern=pattern)
        
        assert "Target content" in result
        assert "Ignore this" not in result


# Test cross_reference_llm
async def test_cross_reference_llm_basic():
    """Test basic cross-referencing functionality"""
    with patch('agent.skills.find_info.manager.ask_llm') as mock_ask_llm:
        mock_ask_llm.return_value = "Summary of the information from sources."
        
        snippets = ["Snippet 1", "Snippet 2", "Snippet 3"]
        result = await cross_reference_llm("test query", snippets)
        
        assert result == "Summary of the information from sources."
        assert mock_ask_llm.called


async def test_cross_reference_llm_limits_sources():
    """Test that cross_reference_llm limits number of sources"""
    with patch('agent.skills.find_info.manager.ask_llm') as mock_ask_llm, \
         patch('agent.skills.find_info.MAX_SOURCES', 2):
        mock_ask_llm.return_value = "Summary"
        
        snippets = ["Snippet 1", "Snippet 2", "Snippet 3", "Snippet 4"]
        result = await cross_reference_llm("test query", snippets)
        
        # Check that prompt only includes first MAX_SOURCES snippets
        call_args = mock_ask_llm.call_args[0][0]
        assert "Snippet 1" in call_args
        assert "Snippet 2" in call_args
        # Snippets 3 and 4 should not be in the prompt
        assert "Snippet 3" not in call_args
        assert "Snippet 4" not in call_args


async def test_cross_reference_llm_truncates_long_snippets():
    """Test that cross_reference_llm truncates long snippets"""
    with patch('agent.skills.find_info.manager.ask_llm') as mock_ask_llm, \
         patch('agent.skills.find_info.MAX_SNIPPET_CHARS', 50):
        mock_ask_llm.return_value = "Summary"
        
        long_snippet = "a" * 100  # Create snippet longer than MAX_SNIPPET_CHARS
        snippets = [long_snippet]
        result = await cross_reference_llm("test query", snippets)
        
        # Check that snippet was truncated in the prompt
        call_args = mock_ask_llm.call_args[0][0]
        assert "[truncated]" in call_args
        # Verify the full long snippet is NOT in the prompt (it should be truncated)
        assert long_snippet not in call_args


# Test DynamicScraper
async def test_dynamic_scraper_find_sources():
    """Test DynamicScraper.find_sources delegates to search_sources_llm"""
    with patch('agent.skills.find_info.search_sources_llm') as mock_search:
        mock_search.return_value = ["https://example.com", "https://test.com"]
        
        scraper = DynamicScraper()
        result = await scraper.find_sources("test query")
        
        assert result == ["https://example.com", "https://test.com"]
        mock_search.assert_called_once_with("test query")


# Test AdaptiveScraper
async def test_adaptive_scraper_analyze_webpage():
    """Test AdaptiveScraper.analyze_webpage loads pattern and scrapes"""
    with patch('agent.skills.find_info.load_scraper_pattern') as mock_load, \
         patch('agent.skills.find_info.scrape_url') as mock_scrape:
        mock_load.return_value = {"main_selector": ".content"}
        mock_scrape.return_value = "Scraped content"
        
        scraper = AdaptiveScraper()
        result = await scraper.analyze_webpage("https://example.com", "test query")
        
        assert result == "Scraped content"
        mock_scrape.assert_called_once_with("https://example.com", pattern={"main_selector": ".content"})


async def test_adaptive_scraper_save_pattern():
    """Test AdaptiveScraper.save_scraper_pattern delegates to save_scraper_pattern"""
    with patch('agent.skills.find_info.save_scraper_pattern') as mock_save:
        scraper = AdaptiveScraper()
        await scraper.save_scraper_pattern(
            url="https://example.com",
            domain="example.com",
            query_type="test",
            content_pattern={"main_selector": ".content"},
            success=True,
            error_msg=None
        )
        
        mock_save.assert_called_once()


# Test find_info end-to-end
async def test_find_info_no_sources_found():
    """Test find_info when no sources are discovered"""
    with patch('agent.skills.find_info.DynamicScraper') as mock_scraper_class:
        mock_scraper = AsyncMock()
        mock_scraper.find_sources.return_value = []
        mock_scraper_class.return_value = mock_scraper
        
        result = await find_info("test query")
        
        assert "couldn't find any sources" in result


async def test_find_info_success():
    """Test successful end-to-end find_info workflow"""
    with patch('agent.skills.find_info.DynamicScraper') as mock_scraper_class, \
         patch('agent.skills.find_info.AdaptiveScraper') as mock_adaptive_class, \
         patch('agent.skills.find_info.cross_reference_llm') as mock_cross_ref:
        
        # Mock DynamicScraper
        mock_scraper = AsyncMock()
        mock_scraper.find_sources.return_value = ["https://example.com", "https://test.com"]
        mock_scraper_class.return_value = mock_scraper
        
        # Mock AdaptiveScraper
        mock_adaptive = AsyncMock()
        mock_adaptive.analyze_webpage.return_value = "Content from source"
        mock_adaptive.save_scraper_pattern = AsyncMock()
        mock_adaptive_class.return_value = mock_adaptive
        
        # Mock cross-reference
        mock_cross_ref.return_value = "Final answer based on sources"
        
        result = await find_info("test query")
        
        assert result == "Final answer based on sources"
        assert mock_scraper.find_sources.called
        assert mock_adaptive.analyze_webpage.call_count == 2  # Called for each URL
        assert mock_adaptive.save_scraper_pattern.call_count == 2  # Pattern saved for each URL
        mock_cross_ref.assert_called_once()


async def test_find_info_with_scraping_errors():
    """Test find_info handles scraping errors gracefully"""
    with patch('agent.skills.find_info.DynamicScraper') as mock_scraper_class, \
         patch('agent.skills.find_info.AdaptiveScraper') as mock_adaptive_class, \
         patch('agent.skills.find_info.cross_reference_llm') as mock_cross_ref:
        
        # Mock DynamicScraper
        mock_scraper = AsyncMock()
        mock_scraper.find_sources.return_value = ["https://example.com"]
        mock_scraper_class.return_value = mock_scraper
        
        # Mock AdaptiveScraper with error
        mock_adaptive = AsyncMock()
        mock_adaptive.analyze_webpage.side_effect = Exception("Connection timeout")
        mock_adaptive.save_scraper_pattern = AsyncMock()
        mock_adaptive_class.return_value = mock_adaptive
        
        # Mock cross-reference
        mock_cross_ref.return_value = "Answer despite errors"
        
        result = await find_info("test query")
        
        # Should still return a result even with scraping errors
        assert result == "Answer despite errors"
        # Error should be saved in pattern
        assert mock_adaptive.save_scraper_pattern.called
        call_args = mock_adaptive.save_scraper_pattern.call_args
        assert call_args[1]['success'] == False


async def test_find_info_saves_successful_patterns():
    """Test that find_info saves patterns for successful scrapes"""
    with patch('agent.skills.find_info.DynamicScraper') as mock_scraper_class, \
         patch('agent.skills.find_info.AdaptiveScraper') as mock_adaptive_class, \
         patch('agent.skills.find_info.cross_reference_llm') as mock_cross_ref:
        
        # Mock DynamicScraper
        mock_scraper = AsyncMock()
        mock_scraper.find_sources.return_value = ["https://example.com"]
        mock_scraper_class.return_value = mock_scraper
        
        # Mock AdaptiveScraper
        mock_adaptive = AsyncMock()
        mock_adaptive.analyze_webpage.return_value = "Good content"
        mock_adaptive.save_scraper_pattern = AsyncMock()
        mock_adaptive_class.return_value = mock_adaptive
        
        # Mock cross-reference
        mock_cross_ref.return_value = "Final answer"
        
        result = await find_info("test query")
        
        # Verify pattern was saved with success=True
        assert mock_adaptive.save_scraper_pattern.called
        call_args = mock_adaptive.save_scraper_pattern.call_args
        assert call_args[1]['success'] == True


async def test_find_info_handles_none_response():
    """Test that find_info handles None response from scraper"""
    with patch('agent.skills.find_info.DynamicScraper') as mock_scraper_class, \
         patch('agent.skills.find_info.AdaptiveScraper') as mock_adaptive_class, \
         patch('agent.skills.find_info.cross_reference_llm') as mock_cross_ref:
        
        # Mock DynamicScraper
        mock_scraper = AsyncMock()
        mock_scraper.find_sources.return_value = ["https://example.com"]
        mock_scraper_class.return_value = mock_scraper
        
        # Mock AdaptiveScraper returning None
        mock_adaptive = AsyncMock()
        mock_adaptive.analyze_webpage.return_value = None
        mock_adaptive.save_scraper_pattern = AsyncMock()
        mock_adaptive_class.return_value = mock_adaptive
        
        # Mock cross-reference
        mock_cross_ref.return_value = "Answer"
        
        result = await find_info("test query")
        
        # Should save pattern with error
        assert mock_adaptive.save_scraper_pattern.called
        call_args = mock_adaptive.save_scraper_pattern.call_args
        assert call_args[1]['success'] == False
        assert "no data returned" in call_args[1]['error_msg']


async def test_find_info_handles_unexpected_response_type():
    """Test that find_info handles unexpected response types from scraper"""
    with patch('agent.skills.find_info.DynamicScraper') as mock_scraper_class, \
         patch('agent.skills.find_info.AdaptiveScraper') as mock_adaptive_class, \
         patch('agent.skills.find_info.cross_reference_llm') as mock_cross_ref:
        
        # Mock DynamicScraper
        mock_scraper = AsyncMock()
        mock_scraper.find_sources.return_value = ["https://example.com"]
        mock_scraper_class.return_value = mock_scraper
        
        # Mock AdaptiveScraper returning unexpected type
        mock_adaptive = AsyncMock()
        mock_adaptive.analyze_webpage.return_value = {"unexpected": "dict"}
        mock_adaptive.save_scraper_pattern = AsyncMock()
        mock_adaptive_class.return_value = mock_adaptive
        
        # Mock cross-reference
        mock_cross_ref.return_value = "Answer"
        
        await find_info("test query")
        
        # Should save pattern with error
        assert mock_adaptive.save_scraper_pattern.called
        call_args = mock_adaptive.save_scraper_pattern.call_args
        assert call_args[1]['success'] == False
        assert "unexpected response type" in call_args[1]['error_msg']


async def run_all_tests():
    """Run all tests"""
    print("Running integration tests for find_info skill...\n")
    
    # search_sources_llm tests
    await test_search_sources_llm_returns_safe_urls()
    print("✓ test_search_sources_llm_returns_safe_urls passed")
    
    await test_search_sources_llm_with_no_urls()
    print("✓ test_search_sources_llm_with_no_urls passed")
    
    await test_search_sources_llm_with_all_unsafe_urls()
    print("✓ test_search_sources_llm_with_all_unsafe_urls passed")
    
    # scrape_url tests
    await test_scrape_url_blocks_unsafe_url()
    print("✓ test_scrape_url_blocks_unsafe_url passed")
    
    await test_scrape_url_success()
    print("✓ test_scrape_url_success passed")
    
    await test_scrape_url_with_redirect()
    print("✓ test_scrape_url_with_redirect passed")
    
    await test_scrape_url_blocks_unsafe_redirect()
    print("✓ test_scrape_url_blocks_unsafe_redirect passed")
    
    await test_scrape_url_with_pattern()
    print("✓ test_scrape_url_with_pattern passed")
    
    # cross_reference_llm tests
    await test_cross_reference_llm_basic()
    print("✓ test_cross_reference_llm_basic passed")
    
    await test_cross_reference_llm_limits_sources()
    print("✓ test_cross_reference_llm_limits_sources passed")
    
    await test_cross_reference_llm_truncates_long_snippets()
    print("✓ test_cross_reference_llm_truncates_long_snippets passed")
    
    # DynamicScraper tests
    await test_dynamic_scraper_find_sources()
    print("✓ test_dynamic_scraper_find_sources passed")
    
    # AdaptiveScraper tests
    await test_adaptive_scraper_analyze_webpage()
    print("✓ test_adaptive_scraper_analyze_webpage passed")
    
    await test_adaptive_scraper_save_pattern()
    print("✓ test_adaptive_scraper_save_pattern passed")
    
    # find_info end-to-end tests
    await test_find_info_no_sources_found()
    print("✓ test_find_info_no_sources_found passed")
    
    await test_find_info_success()
    print("✓ test_find_info_success passed")
    
    await test_find_info_with_scraping_errors()
    print("✓ test_find_info_with_scraping_errors passed")
    
    await test_find_info_saves_successful_patterns()
    print("✓ test_find_info_saves_successful_patterns passed")
    
    await test_find_info_handles_none_response()
    print("✓ test_find_info_handles_none_response passed")
    
    await test_find_info_handles_unexpected_response_type()
    print("✓ test_find_info_handles_unexpected_response_type passed")
    
    print("\n" + "="*60)
    print("All 20 integration tests passed!")
    print("="*60)
    print("\nTest Coverage Summary:")
    print("- search_sources_llm: 3 tests (URL filtering, edge cases)")
    print("- scrape_url: 5 tests (SSRF protection, redirects, patterns)")
    print("- cross_reference_llm: 3 tests (basic, limits, truncation)")
    print("- DynamicScraper: 1 test (delegation)")
    print("- AdaptiveScraper: 2 tests (analyze, save pattern)")
    print("- find_info workflow: 6 tests (end-to-end scenarios)")


if __name__ == "__main__":
    # Run tests using asyncio
    asyncio.run(run_all_tests())
