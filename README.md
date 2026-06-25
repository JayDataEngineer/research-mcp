# Research MCP Server

FastMCP-based web research server: search, scrape, and structured extraction
via the Model Context Protocol. Exposed over Tailscale Serve on the tailnet.

## Features

- **FastMCP Server** — Streamable HTTP transport (`stateless_http=True`)
- **Meta-search** — SearXNG (Brave, Bing, DuckDuckGo) with pagination
- **Smart scraping** — Crawl4AI → httpx → Playwright/Selenium fallback, per-domain
  method learning persisted in PostgreSQL
- **Structured extraction** — pre-built CSS schemas (ecommerce, news, jobs, blog,
  social, products) — no LLM required
- **Deep crawl & URL discovery** — BFS site crawl, sitemap + Common Crawl mapping
- **Documentation fetch** — pull any allowlisted docs site as clean Markdown
- **Caching** — Redis-backed response cache (search 5 min, scrape/docs 1 h)
- **Rate limiting** — per-domain concurrency caps via Redis
- **Proxy support** — optional upstream HTTP/SOCKS proxy with rotation

## Quick Start

```bash
git clone <repo-url> research-mcp
cd research-mcp
cp .env.example .env       # set POSTGRES_PASSWORD (required)
docker compose up -d --build
```

The MCP endpoint listens on `http://localhost:41827/mcp` (host-bound to
`127.0.0.1` only). To expose it on the tailnet (run once on the host):

```bash
sudo tailscale serve --bg --https=10000 --set-path /research http://127.0.0.1:41827
```

Client URL: `https://<node>.ts.net:10000/research/mcp` (replace `<node>` with
your tailnet node name — see `.env.example`).

## Connecting from MCP Clients

### Claude Code

```bash
# Replace <node> with your tailnet node name
claude mcp add --transport http research https://<node>.ts.net:10000/research/mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "research": {
      "type": "http",
      "url": "https://<node>.ts.net:10000/research/mcp"
    }
  }
}
```

### Local (same host only)

```bash
curl -X POST http://localhost:41827/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"probe","version":"1"}}}'
```

## Tools (17 total)

### Search & Scrape

| Tool | Description |
|------|-------------|
| `research(query, max_results=3, depth="quick")` | Search + scrape top results in one call (fastest path to content) |
| `search(query, top_k=5, pages=1, time_filter=null)` | Search engines → titles, URLs, snippets |
| `scrape(url, method=null, css_selector=null, text_only=false)` | Single page → clean Markdown |
| `extract(url, schema_type, custom_selector=null)` | Structured JSON via pre-built schemas |
| `process_html(html, url="")` | Clean raw HTML to Markdown (no fetch) |
| `list_schemas()` | List available extraction schemas |

### Discovery & Crawl

| Tool | Description |
|------|-------------|
| `map(domain, source="sitemap+cc", pattern="*", max_urls=null, query=null)` | Discover URLs via sitemap / Common Crawl |
| `crawl(url, strategy="bfs", max_depth=2, max_pages=50, include_patterns=null, exclude_patterns=null, keywords=null)` | Deep site crawl |

### Documentation

| Tool | Description |
|------|-------------|
| `docs_list_sources()` | Catalog of known documentation libraries |
| `docs_fetch_docs(url)` | Fetch a docs page as Markdown (allowlisted domains only) |

### Admin & Proxy

| Tool | Description |
|------|-------------|
| `domains()` | List tracked domains and their preferred scrape method |
| `stats(hours=24)` | Scrape statistics |
| `reset()` | Clear domain tracking |
| `clear_blacklist()` | Clear blacklist |
| `proxy_status()` | Current proxy configuration and rotation state |
| `proxy_test()` | Probe proxy connectivity (exit IP vs real IP) |
| `proxy_rotate()` | Advance to the next proxy in the rotation |

## Architecture

```
                 Tailscale Serve (:10000 → 127.0.0.1:41827)
                                   │
                 ┌─────────────────▼─────────────────┐
                 │   mcp-server  (FastMCP, :8000)    │
                 │   Streamable HTTP, stateless      │
                 └───────┬───────────────┬───────────┘
                         │               │
                         ▼               ▼
                   ┌──────────┐    ┌──────────┐    ┌──────────┐
                   │ Postgres │    │  Redis   │    │ SearXNG  │
                   │ (domain  │    │ (cache + │    │ (meta    │
                   │  prefs)  │    │  rate)   │    │  search) │
                   └──────────┘    └────┬─────┘    └──────────┘
                                        │
                         ┌──────────────┼──────────────┐
                         ▼              ▼              ▼
                  ┌──────────┐   ┌──────────┐   ┌──────────┐
                  │ celery   │   │ celery   │   │  flower  │
                  │ -worker  │   │  -beat   │   │ (monitor)│
                  └──────────┘   └──────────┘   └──────────┘
                                        │
                                 (optional proxy)
```

## Docker Services

| Container | Purpose |
|-----------|---------|
| `mcp-server` | FastMCP server, Streamable HTTP on container port 8000 |
| `celery-worker` | Background scraping/crawl tasks |
| `celery-beat` | Periodic task scheduler (health checks, blacklist maintenance) |
| `flower` | Celery monitor dashboard (localhost:5555) |
| `postgres` | Domain learning + stats |
| `redis` | Response cache + rate limiting + Celery broker |
| `searxng` | Meta-search backend |

## Configuration

All configurable values live in `.env` (see `.env.example`). Only
`POSTGRES_PASSWORD` is required — every other variable has a sensible
default. The stack ships with **no VPN/proxy dependency**: gluetun,
WireGuard, and friends are entirely optional. If `MCP_PROXY_URL` /
`MCP_PROXY_URLS` are unset, all scraping goes direct and the proxy
tools (`proxy_status` / `proxy_test` / `proxy_rotate`) return a clean
"not configured" message. To route through a proxy, set one of those
env vars to point at any HTTP or SOCKS5 endpoint (gluetun, a
commercial SOCKS provider, etc.).

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | _(required)_ | Postgres password |
| `POSTGRES_DB` | `mcp_server` | Database name |
| `POSTGRES_USER` | `postgres` | Database user |
| `MCP_PROXY_URL` | _(empty)_ | Single HTTP/SOCKS proxy URL for outbound scraping |
| `MCP_PROXY_URLS` | _(empty)_ | Comma-separated list for round-robin rotation (overrides `MCP_PROXY_URL`) |
| `MCP_PROXY_ROTATION` | `round-robin` | `round-robin` or `random` |

The SearXNG config is mounted from [`searxng/settings.yml`](searxng/settings.yml)
— it disables bot-detection for internal callers and enables JSON output.

## Structured Extraction Schemas

| Schema | Extracts |
|--------|----------|
| `ecommerce` | Products (name, price, rating, availability, image, url, sku) |
| `news` | Articles (headline, author, date, content, category, summary) |
| `jobs` | Listings (title, company, location, salary, description, type) |
| `blog` | Posts (title, author, date, content, tags, excerpt) |
| `social` | Social posts (username, content, timestamp, likes, shares) |
| `products` | Multi-item product catalog |

## Documentation Sources

Allowlisted in [`docs_config.example.yaml`](docs_config.example.yaml): langchain, langgraph,
fastmcp, mcp-spec, pydantic, pydantic-ai, docker, nextjs, ai-sdk. Domains
linked from each entry's `llms.txt` are auto-allowed.

## End-to-End Tests

Non-mocked e2e harness against the live server:

```bash
python tests/e2e/test_research.py
# 17 passed, 0 failed
```

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for deployment details.

## Tech Stack

- **FastMCP 3.x** — MCP server framework, Streamable HTTP transport
- **PostgreSQL** — domain tracking and learned scrape preferences
- **Celery + Redis** — task queue, rate limiting, cache
- **SearXNG** — meta-search
- **Crawl4AI / Playwright / SeleniumBase** — multi-strategy scraping
- **Tailscale Serve** — HTTPS at the tailnet edge
