"""MCP Server Tools - Modular organization

This package exports all MCP tools organized by functionality:
- Web tools: research, search, fetch, extract, schemas
- Crawl tools: discover, crawl
- Docs tools: list_docs, read_docs
- Admin tools: domains, stats, reset, unblock
- Proxy tools: proxy_status, test_proxy, rotate_proxy
"""

from .web_tools import research, search, fetch, extract, schemas
from .crawl_tools import discover, crawl
from .docs_tools import list_docs, read_docs
from .admin_tools import domains, stats, reset, unblock
from .proxy_tools import proxy_status, test_proxy, rotate_proxy

__all__ = [
    # Web tools
    "research",
    "search",
    "fetch",
    "extract",
    "schemas",
    # Crawl tools
    "discover",
    "crawl",
    # Docs tools
    "list_docs",
    "read_docs",
    # Admin tools
    "domains",
    "stats",
    "reset",
    "unblock",
    # Proxy tools
    "proxy_status",
    "test_proxy",
    "rotate_proxy",
]
