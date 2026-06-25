# Deployment Guide

Notes for standing up the research-mcp stack on a fresh Linux host.

## Prerequisites

- Docker + Docker Compose v2
- Tailscale (for remote access)
- ~4 GB RAM headroom (the stack peaks around 2 GB)

## Layout

Each MCP server lives in a sibling directory at
`~/Documents/programs/mcp/<name>/`. This server:

```
~/Documents/programs/mcp/research-mcp/
├── docker-compose.yml
├── Dockerfile
├── searxng/settings.yml      # SearXNG config (JSON format, no bot-detection)
├── docs_config.example.yaml  # shipped allowlist for docs_fetch_docs (docs_config.yaml overrides)
├── .env                      # local secrets (not tracked)
└── ...
```

## Bring Up

```bash
cd ~/Documents/programs/mcp/research-mcp
cp .env.example .env
# edit .env — at minimum set POSTGRES_PASSWORD
docker compose up -d --build
```

The MCP endpoint binds to `127.0.0.1:41827` (host only). Verify:

```bash
curl -fsS -X POST http://localhost:41827/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"hc","version":"1"}}}'
```

## Expose on the Tailnet

Run once on the host (not in compose):

```bash
sudo tailscale serve --bg --https=10000 --set-path /research http://127.0.0.1:41827
```

Now reachable at `https://<node>.ts.net:10000/research/mcp` (replace `<node>`
with your tailnet node name — see `.env.example`).

To remove:

```bash
sudo tailscale serve --set-path /research --
```

## Health

The compose `healthcheck` posts a JSON-RPC `initialize` to the MCP endpoint.
The Celery beat schedule also runs `tasks.periodic.health_check` against
Postgres / Redis / SearXNG.

## Troubleshooting

- **`search` returns 0 results** — SearXNG is probably using stock defaults
  that block internal clients. Confirm `searxng/settings.yml` is mounted
  (`limiter: false`, `botdetection.*.enabled: false`, `formats: [html, json, csv, rss]`).
- **`docs_fetch_docs` returns "URL not allowed"** — the tool only accepts URLs
  on domains allowlisted in `docs_config.yaml` (or `docs_config.example.yaml`
  fallback) — or auto-discovered via llms.txt.
- **Scraped content empty** — try `method=crawl4ai` explicitly; some domains
  need JS rendering. The server learns working methods per-domain.
