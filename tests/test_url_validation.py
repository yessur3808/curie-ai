# tests/test_url_validation.py

import sys
import os

# Add parent directory to path to import agent modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agent.skills.find_info import is_safe_url


def test_block_localhost():
    """Test that localhost and loopback addresses are blocked"""
    assert is_safe_url("http://localhost:8000") == False
    assert is_safe_url("http://127.0.0.1") == False
    assert is_safe_url("http://127.0.0.1:8080") == False
    assert is_safe_url("http://[::1]") == False
    assert is_safe_url("http://0.0.0.0") == False


def test_block_private_ips():
    """Test that private IP ranges are blocked"""
    assert is_safe_url("http://10.0.0.1") == False
    assert is_safe_url("http://172.16.0.1") == False
    assert is_safe_url("http://192.168.1.1") == False
    assert is_safe_url("http://192.168.0.100:3000") == False


def test_block_link_local():
    """Test that link-local addresses (including cloud metadata endpoints) are blocked"""
    assert is_safe_url("http://169.254.169.254") == False
    assert is_safe_url("http://169.254.1.1") == False


def test_block_invalid_schemes():
    """Test that non-http/https schemes are blocked"""
    assert is_safe_url("file:///etc/passwd") == False
    assert is_safe_url("ftp://example.com") == False
    assert is_safe_url("javascript:alert(1)") == False
    assert is_safe_url("data:text/html,<script>alert(1)</script>") == False


def test_block_missing_hostname():
    """Test that URLs without hostnames are blocked"""
    assert is_safe_url("http://") == False
    assert is_safe_url("https://") == False


if __name__ == "__main__":
    # Run tests manually
    
    test_block_localhost()
    print("✓ test_block_localhost passed")
    
    test_block_private_ips()
    print("✓ test_block_private_ips passed")
    
    test_block_link_local()
    print("✓ test_block_link_local passed")
    
    test_block_invalid_schemes()
    print("✓ test_block_invalid_schemes passed")
    
    test_block_missing_hostname()
    print("✓ test_block_missing_hostname passed")
    
    print("\nAll tests passed!")

