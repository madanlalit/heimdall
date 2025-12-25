"""
Domain utilities - URL and domain validation for Heimdall.
"""

import fnmatch
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def is_url_allowed(url: str, allowed_domains: list[str]) -> bool:
    """
    Check if a URL is allowed based on the allowed_domains list.

    Args:
        url: The URL to check
        allowed_domains: List of allowed domain patterns

    Returns:
        True if allowed, False otherwise

    Examples:
        allowed_domains = ["chatgpt.com", "*.openai.com"]
        is_url_allowed("https://chatgpt.com/chat", allowed_domains)  # True
        is_url_allowed("https://api.openai.com", allowed_domains)    # True
        is_url_allowed("https://google.com", allowed_domains)        # False
    """
    # If no allowed domains specified, allow all
    if not allowed_domains:
        return True

    # Always allow internal browser pages
    if url in ["about:blank", "chrome://new-tab-page/", "chrome://newtab/"]:
        return True

    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Get hostname
    host = parsed.hostname
    if not host:
        return False

    # Check each allowed domain pattern
    for pattern in allowed_domains:
        if _matches_domain(host, pattern):
            return True

    return False


def _matches_domain(host: str, pattern: str) -> bool:
    """Check if a host matches a domain pattern."""
    # Normalize to lowercase
    host = host.lower()
    pattern = pattern.lower()

    # Remove protocol if present in pattern
    if "://" in pattern:
        pattern = pattern.split("://")[1]

    # Handle www variants
    host_variants = [host]
    if host.startswith("www."):
        host_variants.append(host[4:])
    else:
        host_variants.append(f"www.{host}")

    # Handle wildcard patterns
    if pattern.startswith("*."):
        # *.example.com matches example.com and sub.example.com
        domain_part = pattern[2:]
        for h in host_variants:
            if h == domain_part or h.endswith("." + domain_part):
                return True
    elif "*" in pattern:
        # Other glob patterns
        for h in host_variants:
            if fnmatch.fnmatch(h, pattern):
                return True
    else:
        # Exact match
        for h in host_variants:
            if h == pattern:
                return True

    return False


def extract_domain_from_url(url: str) -> str | None:
    """Extract domain from URL."""
    try:
        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None
