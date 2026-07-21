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
| 4 | Deploy behind Traefik on the real VPS | ✅ Done |
| 5 | Wire Hermes-Agent to call `openbrain-mcp` | ✅ Done |
| 6 | Connect Claude Desktop / Claude Code | ✅ Done |
| 7 | End-to-end acceptance on the real stack | ✅ Done |

Full details, every design decision, and a running log of bugs found/fixed during implementation:
- Design spec — [`docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md`](docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md)
- Implementation plan — [`docs/superpowers/plans/2026-06-30-hermes-openbrain-memory.md`](docs/superpowers/plans/2026-06-30-hermes-openbrain-memory.md)

## Architecture

```
          WhatsApp                      Phone call
          │                             │
          │                             ▼
          │                             Twilio (PSTN, inbound)
          │                             │  POST /voice/inbound
          │                             ▼
          │                             Traefik (host net, file-provider /voice/*)
          │                             │  http://<bridge-ip>:8765
          ▼                             ▼
   ┌────────────────────────────────────────────┐
   │           hermes-agent container           │
   │  WhatsApp bot (existing)                   │
   │  Voice server (FastAPI, port 8765)         │
   │    STT: Twilio speech recognition (de-DE)  │
   │    TTS: edge-tts (de-AT-JonasNeural)       │
   └────────────────────────────────────────────┘
                         │  MCP (internal Docker network)
                         │  http://openbrain-mcp:8080/mcp
                         │  Authorization: Bearer <token>
                         ▼
                                               ┌──────────────┐      ┌────────────┐
                                               │ openbrain-mcp │ ───▶ │ openbrain-db │
                                               │   (Python)    │      │ Postgres 16  │
                                               └──────┬───────┘      │  + pgvector  │
                                                      │              └────────────┘
                      Traefik (HTTPS, Docker-label route, host network)
                brain.<vps-host>.hstgr.cloud   ◀──────┘
                ▲
                │  Authorization: Bearer <token>
                Claude Desktop / Claude Code
                     (laptop, remote MCP)
```

The `hermes-agent` container runs two independent processes: the existing WhatsApp bot, and a
FastAPI voice server (port 8765) added for Twilio inbound calls. Both drive the same underlying
Hermes agent and can both call `openbrain-mcp` over the shared internal Docker network. Traefik
fronts both entry points — WhatsApp doesn't go through Traefik at all (it's driven by the bot's own
outbound connection to WhatsApp), while the phone call and the `openbrain-mcp`/laptop-client
traffic each reach Traefik via a different provider (file-provider route for `/voice/*`,
Docker-label route for `brain.<vps-host>.hstgr.cloud`). Full Twilio details: [`TwilioDocu.md`](TwilioDocu.md).

Two new containers join the existing `hermes-agent` + `traefik` stack:

- **`openbrain-db`** — Postgres 16 + the `pgvector` extension. Owns one table, `captures`. Never
  published, never on Hermes' network — reachable only from `openbrain-mcp`, enforced at the
  Docker network layer (not just by convention).
- **`openbrain-mcp`** — a small Python service. Loads a local multilingual sentence-embedding
  model once (`intfloat/multilingual-e5-small`, 384-dim) and exposes eight tools over the
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

## The eight MCP tools

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
| `find_near_duplicates(threshold=0.95, limit=50)` | Read-only. Lists capture pairs whose summaries are near-duplicates by embedding cosine similarity — catches near-duplicates the exact-fingerprint dedup in `save` misses. Delete one side of a pair via the existing `delete` tool. |
| `compute_fingerprint(raw_text, source_url?)` | Read-only, no DB access. Shows the SHA-256 dedup fingerprint `save` would compute for this input, plus the normalized string it's based on — for debugging the fingerprint mechanism, not for checking against existing captures. |

All except `save`/`update`'s pass-through of `metadata` are exercised by the test suite
(`openbrain-mcp/tests/`, 24 tests, mostly run against a real Postgres+pgvector instance;
`compute_fingerprint`'s tests are the exception and need no database).

## Repository layout

```
openbrain-mcp/
  app/
    config.py      # env-var loading
    keywords.py     # normalize_keywords() — trim/dedupe/cap keyword lists
    fingerprint.py   # content_fingerprint() / content_fingerprint_debug() — SHA-256 dedup key
    embeddings.py     # e5 model wrapper (passage:/query: prefixes)
    db.py              # get_conn() — psycopg + pgvector registration
    store.py            # save/search/recent/stats/delete/update/find_near_duplicates — the only file with SQL
    server.py             # the 8 MCP tools + bearer auth + /health
  migrations/001_init.sql   # schema
  tests/                     # 24 tests, pytest
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
`http://localhost:8080/mcp` with that bearer token and call the eight tools directly — useful for
testing before Hermes/laptop wiring exists (Phases 5-6).

Run the test suite against a live database:

```bash
DATABASE_URL="postgresql://openbrain:<password>@localhost:5432/openbrain" \
  python -m pytest openbrain-mcp/tests/ -v
```

(Note: the compose file doesn't publish Postgres' port by default — either run pytest from a
container on `openbrain_internal`, or add a temporary port mapping via a local, gitignored
`docker-compose.override.yml`.)

## Phase 4 — Deployed behind Traefik on the VPS

The two containers are live on the real server (`brain.srv1608402.hstgr.cloud`), both `healthy`,
and reachable over real HTTPS with a valid Let's Encrypt cert:

- `curl https://$OPENBRAIN_HOST/health` → `{"ok":true}` (valid cert, no TLS warning)
- `curl https://$OPENBRAIN_HOST/mcp` with no token → `401`; with the bearer token → not `401`
  (`406` is expected for a bare `curl GET` — MCP's Streamable HTTP endpoint requires proper
  `Accept`/session headers; the point of that check is only that auth isn't rejecting it)

`docker compose -f docker-compose.openbrain.yml up -d --build` was run on the VPS from a fresh
`git clone`, with `deploy/.env` filled in from `.env.example` (real `POSTGRES_PASSWORD`,
`OPENBRAIN_TOKEN`, and `OPENBRAIN_HOST`). Traefik picked up the compose labels automatically —
no Traefik config changes were needed.

## Phase 5 — Hermes-Agent wired to OpenBrain

Live since 2026-07-18. `openbrain-mcp` is registered as an MCP server inside Hermes'
own configuration (internal address `http://openbrain-mcp:8080/mcp`, no TLS hop needed — Hermes
and `openbrain-mcp` share a Docker network), and Hermes has a capture directive saved as a
persistent Skill (`openbrain-capture`): on receiving a link/note it fetches, summarizes, pulls ~5
keywords, and calls `save`; on a recall request it calls `search`.

Verified end-to-end over real WhatsApp messages (not a self-triggered test):
- **Capture:** sent a Substack link → Hermes replied with a German summary + 5 keywords → `psql`
  confirmed the row (`source=substack`, correct `source_url`, matching summary/keywords, fresh
  `created_at`).
- **Recall:** asked Hermes to find the note using deliberately different wording than the
  original → it returned the correct summary and source URL, confirming semantic search (not a
  keyword-string match).

Note: registering the MCP server via direct `config.yaml` edits on the host did not survive a
Hermes container restart (the Hostinger-managed image appears to regenerate `mcp_servers` from
another source of truth on boot — not fully root-caused). Both the MCP registration and the
capture directive ended up being set via Hermes' own in-container tooling (`hermes mcp configure`)
and a WhatsApp chat instruction (saved as a Skill) instead of hand-edited config files.

## Phase 6 — Laptop clients

Independent of Phase 5 — it only needs Phase 4's public HTTPS endpoint, not Hermes.

**Claude Code: done (2026-07-18).** Registered as a remote `http`-transport MCP server pointed at
`https://<OPENBRAIN_HOST>/mcp` with the bearer token as a header. Getting there took real
debugging: `claude mcp add`'s `--header` flag silently doesn't persist on Windows (CLI v2.1.214,
both `http` and `stdio` transport) — it's accepted with no error, but the stored config ends up
with no header at all, which later surfaces as a generic `-32000: Connection closed`. Confirmed via
a direct authenticated HTTP request (bypassing the CLI) that the token/server/network were all
fine, isolating the bug to the CLI's own header handling. Fixed by writing the `headers` field
directly into `~/.claude.json` rather than going through `claude mcp add`. Full writeup in the
plan's Task 6.2 resolution note.

**Claude Desktop: done (2026-07-18).** Took a different fix than Claude Code, because this Desktop
version doesn't support Claude Code's native `"type": "http"` config shape at all — it silently
schema-rejects and prunes it, which was the real reason the config kept appearing to "lose" its
`openbrain` entry earlier. Desktop only accepts the stdio config shape, so the fix goes through the
`mcp-remote` bridge, combined with a Windows-specific fix: `mcp-remote` mangles a `--header` value
that contains a space (e.g. `"Authorization: Bearer <token>"`), so the token has to live in an
environment variable referenced as `${AUTH_HEADER}` inside a space-free header arg
(`Authorization:${AUTH_HEADER}`). A second, unrelated bug (`Set-Content -Encoding utf8` writing a
BOM that Desktop's JSON parser rejects) also had to be fixed by writing the config file with
`[System.IO.File]::WriteAllText(...)` instead. Full writeup in the plan's Task 6.1 resolution note.

## Using it

- **From WhatsApp (live):** send a link or a note to Hermes. It replies confirming what it stored
  (summary + keywords), or that it was already saved if you sent the same link again. Later, ask
  Hermes to recall something — in different words than you sent it, even a different language —
  and it answers from what's stored.
- **From Claude Code (live):** ask directly — *"search my brain for the thing I saved about X"* —
  and it uses the same `search` tool over the same database.
- **From Claude Desktop (live):** same idea, using the `mcp-remote` stdio bridge instead of a
  native remote entry.

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

## Related: Hermes Voice (Twilio)

A separate, parallel channel on the same VPS — not part of OpenBrain, but deployed alongside it on
the same Hermes-Agent host. Full details: [`TwilioDocu.md`](TwilioDocu.md).

> Status: **Live & productive** (since 2026-07-17) — Number: **+43 1 4351876**

Calling that number reaches Hermes over the phone: Twilio's built-in speech recognition (`de-DE`)
transcribes what you say, a FastAPI voice server (running as a second process inside the existing
Hermes container, port 8765) forwards it to the Hermes CLI with per-call session context (keyed by
Twilio's `CallSid`), and the reply is spoken back via `edge-tts` (`de-AT-JonasNeural`). It runs
independently of the WhatsApp channel and doesn't replace it.

Routing is the interesting part: Traefik reaches the voice server directly over the Docker bridge
IP via a **file-provider** route (`/docker/traefik/dynamic/voice.yml`), added alongside its
existing Docker-label-based routes — the live Hermes/WhatsApp container was never touched or
rebuilt to wire this up, and the existing Let's Encrypt certificate volume was preserved across
the Traefik recreate. Every inbound Twilio webhook is validated via `X-Twilio-Signature`; Twilio
credentials live only in `~/.hermes/.env` (mode `600`), never in code or docs.
