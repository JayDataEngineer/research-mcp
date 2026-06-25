#!/usr/bin/env python3
"""End-to-end tests for the research MCP server.

Calls every tool with real (non-mocked) inputs against the deployed
server. Network-dependent (SearXNG + public scrapers), so flakiness is
possible — the runner annotates each result with timing.

Usage:
  python tests/e2e/test_research.py
  python tests/e2e/test_research.py --url http://localhost:41827/mcp
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from mcp_client import McpClient, ToolResult  # noqa: E402

DEFAULT_URL = os.environ.get(
    "RESEARCH_MCP_URL",
    "http://localhost:41827/mcp",
)

# Stable test fixtures
TEST_QUERY = "python programming language"
TEST_SCRAPE_URL = "https://example.com/"           # trivially scrapable, stable
TEST_MAP_DOMAIN = "example.com"
TEST_DOCS_QUERY = "python requests library"

# Tools that mutate server state — call last
MUTATING_TOOLS = {"reset", "clear_blacklist", "proxy_rotate"}


def make_tests() -> list[tuple[str, dict, str]]:
    return [
        # --- Read-only inspection tools ---
        ("list_schemas", {}, "list extraction schemas"),
        ("domains", {}, "list tracked domains (may be empty)"),
        ("stats", {}, "scrape statistics"),
        ("proxy_status", {}, "proxy configuration"),
        ("docs_list_sources", {}, "documentation sources catalog"),

        # --- Search/scrape (network-dependent) ---
        ("search", {"query": TEST_QUERY, "top_k": 3}, "SearXNG meta-search"),
        ("research", {"query": TEST_QUERY, "max_results": 2}, "search + scrape top results"),
        ("scrape", {"url": TEST_SCRAPE_URL}, "scrape a single page"),
        ("map", {"domain": TEST_MAP_DOMAIN, "max_urls": 5}, "discover URLs via sitemap"),
        ("process_html",
         {"html": "<html><head><title>Test</title></head><body><article>"
                  "<h1>Hello World</h1>"
                  "<p>This is a test paragraph with enough text to survive cleaning.</p>"
                  "<p>Second paragraph of substantive content.</p>"
                  "</article></body></html>",
          "url": "https://example.com/article"},
         "clean raw HTML to markdown"),
        ("extract",
         {"url": TEST_SCRAPE_URL, "schema_type": "blog"},
         "structured extraction (example.com is thin — may return empty)"),
        ("docs_fetch_docs",
         {"url": "https://docs.pydantic.dev/latest/"},
         "fetch docs page as markdown (must be a known docs domain)"),

        # --- Deep crawl (bounded; example.com is tiny so this stays fast) ---
        ("crawl",
         {"url": "https://example.com/", "max_pages": 2, "max_depth": 1},
         "bounded deep crawl of example.com"),

        # --- Proxy tools (proxy_test hits the network) ---
        ("proxy_test", {}, "test proxy connectivity (may fail if no proxy configured)"),

        # --- Mutating tools — run last so they don't disturb earlier reads ---
        ("clear_blacklist", {}, "clear blacklist (no-op if empty)"),
        ("reset", {}, "reset domain tracking (no-op if empty)"),
        ("proxy_rotate", {}, "rotate proxy (no-op if single proxy)"),
    ]


def validate(tool: str, r: ToolResult) -> tuple[bool, str]:
    if not r.success:
        return False, f"MCP error: {r.error}"
    if r.tool_success is False:
        err = (r.structured.get("error", "") or "")[:150] if isinstance(r.structured, dict) else ""
        # proxy_test / proxy_rotate legitimately fail when no proxy is configured
        if tool in {"proxy_test", "proxy_rotate"} and (
            "no proxy" in err.lower() or "not configured" in err.lower() or "not set" in err.lower()
        ):
            return True, f"correctly reported no proxy: {err[:80]}"
        return False, f"tool failed: {err}"
    # Tool-specific structure checks
    s = r.structured
    if tool == "search" and isinstance(s, dict):
        results = s.get("results", [])
        if len(results) == 0:
            return False, "search returned 0 results (SearXNG backend issue?)"
        return True, f"{len(results)} results"
    if tool == "research" and isinstance(s, dict):
        results = s.get("results", [])
        return True, f"{len(results)} results (research can legitimately be 0 for obscure queries)"
    if tool == "scrape" and isinstance(s, dict):
        content = s.get("content", "") or s.get("markdown", "")
        if not content:
            return False, "scrape returned empty content"
        return True, f"{len(content)} chars scraped"
    if tool == "process_html" and isinstance(s, dict):
        content = s.get("content", "") or s.get("markdown", "")
        if "Hello World" not in content:
            return False, f"expected 'Hello' in output, got: {content[:100]!r}"
        return True, "markdown contains expected text"
    if tool == "map" and isinstance(s, dict):
        urls = s.get("urls", [])
        return True, f"{len(urls)} URLs discovered"
    if tool == "crawl" and isinstance(s, dict):
        pages = s.get("pages", s.get("results", []))
        if not isinstance(pages, list):
            pages = []
        # example.com is a single-page site; 0+ pages is acceptable as long as
        # the call didn't error — we mainly want to exercise the crawl path.
        return True, f"{len(pages)} page(s) crawled"
    return True, "ok"


BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    print(f"{BOLD}research-mcp e2e{RESET} against {args.url}\n")

    client = McpClient(args.url, timeout=args.timeout)
    init = client.initialize()
    if "error" in init:
        print(f"{RED}initialize failed: {init['error']}{RESET}")
        return 2
    info = init.get("result", {}).get("serverInfo", {})
    print(f"  server: {info.get('name','?')} {info.get('version','?')}")
    tools = client.list_tools()
    print(f"  tools online: {len(tools)}\n")

    tests = make_tests()
    passed = failed = 0
    failures: list[str] = []

    for tool, targs, desc in tests:
        arg_preview = next(iter(targs.values()), "") if targs else ""
        arg_str = str(arg_preview)[:30]
        print(f"  {tool+'(' + arg_str + ')':<48} {DIM}{desc[:50]}{RESET}")
        t0 = time.time()
        try:
            r = client.call(tool, targs)
        except Exception as e:
            print(f"    {RED}EXCEPTION after {time.time()-t0:.1f}s: {type(e).__name__}: {e}{RESET}")
            failed += 1
            failures.append(f"{tool}: exception {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        ok, msg = validate(tool, r)
        if ok:
            print(f"    {GREEN}PASS{RESET} ({dt:.1f}s) — {msg}")
            passed += 1
        else:
            print(f"    {RED}FAIL{RESET} ({dt:.1f}s) — {msg}")
            failed += 1
            failures.append(f"{tool}: {msg}")

    print(f"\n{BOLD}Summary{RESET}: {GREEN}{passed} passed{RESET}, {RED}{failed} failed{RESET}")
    if failures:
        print(f"\n{RED}Failures:{RESET}")
        for f in failures:
            print(f"  - {f}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
