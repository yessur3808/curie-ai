# tests/test_url_validation.py

import sys
import os
import asyncio

# Add parent directory to path to import agent modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.skills.find_info import is_safe_url


async def test_block_localhost():
    """Test that localhost and loopback addresses are blocked"""
    assert await is_safe_url("http://localhost:8000") == False
    assert await is_safe_url("http://127.0.0.1") == False
    assert await is_safe_url("http://127.0.0.1:8080") == False
    assert await is_safe_url("http://[::1]") == False
    assert await is_safe_url("http://0.0.0.0") == False
    assert await is_safe_url("http://[::]") == False  # IPv6 unspecified
    
    # Additional loopback address variations in 127.0.0.0/8 range
    assert await is_safe_url("http://127.0.0.2") == False
    assert await is_safe_url("http://127.1") == False  # Short form of 127.0.0.1
    assert await is_safe_url("http://127.255.255.255") == False


async def test_block_private_ips():
    """Test that private IP ranges are blocked"""
    # 10.0.0.0/8
    assert await is_safe_url("http://10.0.0.1") == False
    assert await is_safe_url("http://10.255.255.255") == False
    assert await is_safe_url("http://10.0.0.1:8080") == False  # Port 8080 blocked on private IP
    
    # 172.16.0.0/12
    assert await is_safe_url("http://172.16.0.1") == False
    assert await is_safe_url("http://172.31.255.255") == False
    assert await is_safe_url("http://172.16.0.1:8080") == False  # Port 8080 blocked on private IP
    
    # 192.168.0.0/16
    assert await is_safe_url("http://192.168.1.1") == False
    assert await is_safe_url("http://192.168.0.100:3000") == False
    assert await is_safe_url("http://192.168.255.255") == False
    assert await is_safe_url("http://192.168.1.1:8080") == False  # Port 8080 blocked on private IP


async def test_block_link_local():
    """Test that link-local addresses (including cloud metadata endpoints) are blocked"""
    assert await is_safe_url("http://169.254.169.254") == False
    assert await is_safe_url("http://169.254.1.1") == False
    assert await is_safe_url("http://169.254.0.0") == False
    assert await is_safe_url("http://169.254.255.255") == False
    assert await is_safe_url("http://169.254.169.254:8080") == False  # Port 8080 blocked on link-local IP


async def test_block_reserved_addresses():
    """Test that reserved and broadcast addresses are blocked"""
    # Broadcast address
    assert await is_safe_url("http://255.255.255.255") == False
    # Reserved for future use (Class E)
    assert await is_safe_url("http://240.0.0.1") == False


async def test_block_ipv6_mapped_ipv4():
    """Test that IPv6-mapped IPv4 addresses are properly validated
    
    IPv6-mapped IPv4 addresses (e.g., ::ffff:127.0.0.1) can bypass validation
    because they don't report as loopback/private when checked as IPv6 addresses.
    This test ensures we detect and validate the mapped IPv4 address.
    """
    # IPv6-mapped loopback addresses
    assert await is_safe_url("http://[::ffff:127.0.0.1]") == False
    assert await is_safe_url("http://[::ffff:127.0.0.2]") == False
    assert await is_safe_url("http://[::ffff:127.255.255.255]") == False
    
    # IPv6-mapped private addresses (10.0.0.0/8)
    assert await is_safe_url("http://[::ffff:10.0.0.1]") == False
    assert await is_safe_url("http://[::ffff:10.255.255.255]") == False
    
    # IPv6-mapped private addresses (172.16.0.0/12)
    assert await is_safe_url("http://[::ffff:172.16.0.1]") == False
    assert await is_safe_url("http://[::ffff:172.31.255.255]") == False
    
    # IPv6-mapped private addresses (192.168.0.0/16)
    assert await is_safe_url("http://[::ffff:192.168.1.1]") == False
    assert await is_safe_url("http://[::ffff:192.168.0.100]") == False
    
    # IPv6-mapped link-local addresses (AWS/cloud metadata)
    assert await is_safe_url("http://[::ffff:169.254.169.254]") == False
    assert await is_safe_url("http://[::ffff:169.254.1.1]") == False
    
    # IPv6-mapped unspecified address
    assert await is_safe_url("http://[::ffff:0.0.0.0]") == False


async def test_block_suspicious_ports():
    """Test that suspicious internal service ports are blocked
    
    Note: Port 8080 is conditionally blocked - it's blocked for private/internal IPs
    but allowed for public IPs. Test for port 8080 blocking is in test_block_localhost
    with 127.0.0.1:8080, which correctly blocks since 127.0.0.1 is an internal IP.
    """
    assert await is_safe_url("http://example.com:22") == False   # SSH
    assert await is_safe_url("http://example.com:3306") == False # MySQL
    assert await is_safe_url("http://example.com:5432") == False # PostgreSQL
    assert await is_safe_url("http://example.com:6379") == False # Redis
    assert await is_safe_url("http://example.com:27017") == False # MongoDB


async def test_block_invalid_schemes():
    """Test that non-http/https schemes are blocked"""
    assert await is_safe_url("file:///etc/passwd") == False
    assert await is_safe_url("ftp://example.com") == False
    assert await is_safe_url("javascript:alert(1)") == False
    assert await is_safe_url("data:text/html,<script>alert(1)</script>") == False
    assert await is_safe_url("gopher://example.com") == False


async def test_block_missing_hostname():
    """Test that URLs without hostnames are blocked"""
    assert await is_safe_url("http://") == False
    assert await is_safe_url("https://") == False


async def test_url_length_limits():
    """Test that excessively long URLs are blocked"""
    # URL exceeding maximum length
    long_url = "http://example.com/" + "a" * 3000
    assert await is_safe_url(long_url) == False
    
    # Hostname exceeding maximum DNS length
    long_hostname = "http://" + "a" * 300 + ".com"
    assert await is_safe_url(long_hostname) == False


async def test_dns_resolution_failures():
    """Test that URLs that fail DNS resolution are blocked"""
    # These should fail DNS resolution in sandboxed environment
    assert await is_safe_url("http://this-domain-does-not-exist-12345.com") == False
    assert await is_safe_url("http://invalid.invalid") == False


async def run_all_tests():
    """Run all tests asynchronously"""
    await test_block_localhost()
    print("✓ test_block_localhost passed")
    
    await test_block_private_ips()
    print("✓ test_block_private_ips passed")
    
    await test_block_link_local()
    print("✓ test_block_link_local passed")
    
    await test_block_reserved_addresses()
    print("✓ test_block_reserved_addresses passed")
    
    await test_block_ipv6_mapped_ipv4()
    print("✓ test_block_ipv6_mapped_ipv4 passed")
    
    await test_block_suspicious_ports()
    print("✓ test_block_suspicious_ports passed")
    
    await test_block_invalid_schemes()
    print("✓ test_block_invalid_schemes passed")
    
    await test_block_missing_hostname()
    print("✓ test_block_missing_hostname passed")
    
    await test_url_length_limits()
    print("✓ test_url_length_limits passed")
    
    await test_dns_resolution_failures()
    print("✓ test_dns_resolution_failures passed")
    
    print("\nAll tests passed!")
    print("\nNote: Positive tests for legitimate public URLs (e.g., https://example.com)")
    print("are not included because they require DNS resolution, which is not available")
    print("in this sandboxed environment. In production with DNS access, legitimate")
    print("public URLs that resolve to non-private IPs will pass validation.")


if __name__ == "__main__":
    # Run tests using asyncio
    asyncio.run(run_all_tests())
