# HermesPlusOpenbrain

A self-hosted, [OpenBrain](https://github.com/RadixSeven/OpenBrain)-style **secondary semantic
memory** for a self-hosted **Hermes-Agent** WhatsApp bot.

**Use case:** send a link (YouTube, Substack, …) or a note to Hermes-Agent over WhatsApp → Hermes
reads it, writes a summary, and extracts ~5 keywords → it's stored in a self-hosted Postgres +
pgvector database on the same VPS → later, ask for it back — from WhatsApp, or from a laptop via
Claude Desktop / Claude Code — using natural language. Retrieval is by **meaning**, not keyword
match: a query worded completely differently from the original note still finds it.

No third-party memory SaaS, no API metering — the database and the embedding model both run on
infrastructure you already own.

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Resolve VPS/network prerequisites | ✅ Done |
| 1 | Postgres + pgvector schema | ✅ Done |
| 2 | `openbrain-mcp` service (6 MCP tools, TDD, 18 tests) | ✅ Done |
| 3 | Containerize (Dockerfile + Compose), verified end-to-end locally | ✅ Done |
| 4 | Deploy behind Traefik on the real VPS | ⬜ Not started |
| 5 | Wire Hermes-Agent to call `openbrain-mcp` | ⬜ Not started |
| 6 | Connect Claude Desktop / Claude Code | ⬜ Not started |
| 7 | End-to-end acceptance on the real stack | ⬜ Not started |

Full details, every design decision, and a running log of bugs found/fixed during implementation:
- Design spec — [`docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md`](docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md)
- Implementation plan — [`docs/superpowers/plans/2026-06-30-hermes-openbrain-memory.md`](docs/superpowers/plans/2026-06-30-hermes-openbrain-memory.md)

## Architecture

```
                      WhatsApp
                         │
                         ▼
   ┌─────────────┐   MCP (internal Docker network)   ┌──────────────┐      ┌────────────┐
   │ hermes-agent │ ───────────────────────────────▶ │ openbrain-mcp │ ───▶ │ openbrain-db │
   │  (existing)  │  http://openbrain-mcp:8080/mcp    │   (Python)    │      │ Postgres 16  │
   └─────────────┘   Authorization: Bearer <token>    └──────┬───────┘      │  + pgvector  │
                                                               │             └────────────┘
                          Traefik (HTTPS, host network)        │
                     brain.<vps-host>.hstgr.cloud   ◀──────────┘
                                 ▲
                                 │  Authorization: Bearer <token>
                         Claude Desktop / Claude Code
                              (laptop, remote MCP)
```

Two new containers join the existing `hermes-agent` + `traefik` stack:

- **`openbrain-db`** — Postgres 16 + the `pgvector` extension. Owns one table, `captures`. Never
  published, never on Hermes' network — reachable only from `openbrain-mcp`, enforced at the
  Docker network layer (not just by convention).
- **`openbrain-mcp`** — a small Python service. Loads a local multilingual sentence-embedding
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes six tools over the
  [Model Context Protocol](https://modelcontextprotocol.io) (Streamable HTTP transport). Sits on
  *two* Docker networks: `openbrain_internal` (talks to the db) and Hermes' own network (so Hermes
  can call it by container name), and is fronted by Traefik for TLS + remote laptop access.
  Every request except `/health` requires a bearer token — that token, not network topology, is
  the actual access-control boundary once the service is reachable from the internet.

**Why build instead of reuse:** the canonical [OB1](https://github.com/NateBJones-Projects/OB1)
self-host paths are Supabase or Kubernetes — neither fits comfortably on an 8GB VPS already running
Hermes. This is a deliberately lean, single-purpose service instead.

## Data model

One table, `captures` (see [`openbrain-mcp/migrations/001_init.sql`](openbrain-mcp/migrations/001_init.sql)):

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `raw_text` | `text` | The original content (transcript, article text, or the pasted message) |
| `summary` | `text` | What actually gets embedded and semantically searched |
| `keywords` | `text[]` | ~5 keywords, deduped case-insensitively, capped at 8 |
| `source` | `text` | e.g. `youtube`, `substack`, `other` |
| `source_url` | `text` | Original link, if any |
| `lang` | `text` | Optional |
| `metadata` | `jsonb` | Freeform extension field (people, topics, action items, …). Currently write-only — `save`/`update` accept and persist it, but no tool reads it back (`search`/`list_recent` don't select it, and there's no get-by-id). |
| `fingerprint` | `text` | SHA-256 dedup key — normalized URL (tracking params/`www.` stripped) or normalized text |
| `embedding` | `vector(384)` | HNSW cosine-distance index for semantic search |
| `created_at` / `updated_at` | `timestamptz` | |

Sending the same link twice (WhatsApp forwards routinely carry different tracking parameters,
e.g. YouTube's `?si=`) is deduped via `fingerprint` — the second send returns the existing row
instead of creating a duplicate.

## The six MCP tools

Implemented in [`openbrain-mcp/app/server.py`](openbrain-mcp/app/server.py), delegating to
[`openbrain-mcp/app/store.py`](openbrain-mcp/app/store.py):

| Tool | Purpose |
|---|---|
| `save(raw_text, summary, keywords, source?, source_url?, lang?, metadata?)` | Store a note. Idempotent — resending the same link/text returns the existing id (`deduped: true`), no re-embedding. |
| `search(query, k=5)` | Semantic search. Returns the top-k matches by meaning, each with a cosine-similarity `score`. |
| `list_recent(n=10)` | Most recently captured notes, newest first. |
| `stats()` | Total captures, counts by source, first/last capture timestamp. |
| `delete(id)` | Remove a capture (prune a mis-capture). |
| `update(id, summary?, keywords?, metadata?)` | Edit a capture. Changing `summary` re-embeds it. `metadata` is a full replace, not a merge. |

All except `save`/`update`'s pass-through of `metadata` are exercised by the test suite
(`openbrain-mcp/tests/`, 18 tests, run against a real Postgres+pgvector instance).

## Repository layout

```
openbrain-mcp/
  app/
    config.py      # env-var loading
    keywords.py     # normalize_keywords() — trim/dedupe/cap keyword lists
    fingerprint.py   # content_fingerprint() — SHA-256 dedup key
    embeddings.py     # e5 model wrapper (passage:/query: prefixes)
    db.py              # get_conn() — psycopg + pgvector registration
    store.py            # save/search/recent/stats/delete/update — the only file with SQL
    server.py             # the 6 MCP tools + bearer auth + /health
  migrations/001_init.sql   # schema
  tests/                     # 18 tests, pytest
  Dockerfile
  pyproject.toml
deploy/
  docker-compose.openbrain.yml   # openbrain-db + openbrain-mcp, Traefik labels
  .env.example                    # required env vars (no real secrets)
docs/superpowers/
  specs/2026-06-30-hermes-openbrain-memory-design.md    # the "what" and "why"
  plans/2026-06-30-hermes-openbrain-memory.md            # the "how", task-by-task, with a
                                                            revision log of every bug found+fixed
```

## Running it locally

Requires Docker.

```bash
cd deploy
cp .env.example .env
# edit .env: set POSTGRES_PASSWORD (URL-safe — e.g. `openssl rand -hex 32`,
# avoid @ : / # % ?) and OPENBRAIN_TOKEN to any long random string.
# OPENBRAIN_HOST doesn't matter for local use (no Traefik involved).

docker compose -f docker-compose.openbrain.yml up -d --build
docker compose -f docker-compose.openbrain.yml ps   # both services should show (healthy)
```

The embedding model is pinned via `OPENBRAIN_MODEL` (default `intfloat/multilingual-e5-small`,
384-dim) — override it in `.env` if you ever want a different e5-family model, but note the
schema's `vector(384)` column would need to match the new model's output dimension.

> The compose file references Hermes' production Docker network
> (`hermes-agent-7qpk_default`) as external, since on the real VPS `openbrain-mcp` needs to be
> reachable from Hermes by container name. That network only exists there. To bring up the full
> stack locally (not just `openbrain-db` alone), create a stand-in first:
> `docker network create hermes-agent-7qpk_default`.

Once running:

```bash
curl http://localhost:8080/health                                          # {"ok":true}, no auth
curl -H "Authorization: Bearer <your token>" http://localhost:8080/mcp     # MCP endpoint
```

Any MCP client (the `mcp` Python SDK, Claude Desktop, Claude Code) can connect to
`http://localhost:8080/mcp` with that bearer token and call the six tools directly — useful for
testing before Hermes/laptop wiring exists (Phases 5-6).

Run the test suite against a live database:

```bash
DATABASE_URL="postgresql://openbrain:<password>@localhost:5432/openbrain" \
  python -m pytest openbrain-mcp/tests/ -v
```

(Note: the compose file doesn't publish Postgres' port by default — either run pytest from a
container on `openbrain_internal`, or add a temporary port mapping via a local, gitignored
`docker-compose.override.yml`.)

## Deploying to the VPS (Phase 4, not yet executed)

The intended flow, per the plan:

1. `git clone` this repo onto the VPS, `cd deploy`, `cp .env.example .env` and fill in real values
   (`OPENBRAIN_HOST=brain.<your-hostinger-subdomain>.hstgr.cloud` — the wildcard DNS for that
   subdomain already resolves to the VPS, confirmed in Phase 0, no DNS changes needed).
2. `docker compose -f docker-compose.openbrain.yml up -d --build`.
3. Traefik (already running on the VPS in `network_mode: host`, fronting Hermes the same way)
   picks up the compose labels automatically and issues a Let's Encrypt cert for `OPENBRAIN_HOST`
   — no Traefik config changes needed.
4. Register `http://openbrain-mcp:8080/mcp` (internal address, no TLS hop needed) as an MCP server
   inside Hermes' own configuration, with the bearer token as a header (Phase 5).
5. Add a short capture instruction to Hermes: on receiving a link/note, fetch it, summarize, pull
   ~5 keywords, call `save`; on a recall request, call `search` (Phase 5).
6. Add `https://<OPENBRAIN_HOST>/mcp` as a remote MCP server in Claude Desktop / Claude Code, same
   bearer token (Phase 6).

## Using it (once Phases 5-6 are live)

- **From WhatsApp:** send a link or a note to Hermes. It replies confirming what it stored (summary
  + keywords), or that it was already saved if you sent the same link again. Later, ask Hermes to
  recall something — in different words than you sent it, even a different language — and it
  answers from what's stored.
- **From Claude Desktop / Claude Code:** once the remote MCP server is added, ask directly —
  *"search my brain for the thing I saved about X"* — and it uses the same `search` tool over the
  same database.

## Security model

- `openbrain-db` is unreachable from anywhere except `openbrain-mcp` — enforced by Docker network
  membership, not just convention.
- Every `openbrain-mcp` endpoint except `/health` requires `Authorization: Bearer <OPENBRAIN_TOKEN>`.
- TLS is terminated by Traefik; `openbrain-mcp` itself only ever speaks plain HTTP, on the internal
  Docker network.
- No secrets are committed — `deploy/.env` (the real one, not `.env.example`) is gitignored.

See the design spec §6 and the plan's "Known follow-ups" section for lower-priority hardening
ideas already identified but deliberately deferred (e.g. a read-only token for laptop clients vs.
a write token for Hermes, constant-time token comparison).
