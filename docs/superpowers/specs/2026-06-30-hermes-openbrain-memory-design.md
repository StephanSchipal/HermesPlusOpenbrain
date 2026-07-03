# Hermes-Agent + OpenBrain Secondary Memory — Design Spec

**Date:** 2026-06-30
**Author:** Stephan (with Claude Code, brainstorming session)
**Status:** Approved design — ready for implementation planning
**Revised:** 2026-07-03 — reviewed against the canonical OB1 repo; reinforced the build-vs-reuse
rationale and added fingerprint dedup, `delete`/`update` MCP tools, and an Auto-Capture skill reference.

---

## 1. Goal

Add an OpenBrain-style, self-owned, agent-readable **secondary memory** to an existing,
working **Hermes-Agent** deployment. OpenBrain references: the canonical
[OB1 repo](https://github.com/NateBJones-Projects/OB1) (Nate Jones) and the
[RadixSeven fork](https://github.com/RadixSeven/OpenBrain). We reuse OB1's *primitives and
ideas* (Postgres + pgvector, semantic capture, fingerprint dedup, a few MCP tools) but not its
deployment (see §3).

Concretely: information the user sends from **YouTube, Substack, and similar sources** via
**WhatsApp** should be **analyzed, summarized, reduced to around 5 keywords, and stored** in a
self-hosted semantic database. That stored knowledge must then be **searchable by meaning**
from both WhatsApp (via Hermes) and the user's laptop (via Claude Desktop and Claude Code).

This memory is **purpose-built for this capture use case** and is intentionally separate from
Hermes-Agent's own built-in memory (`MEMORY.md` / `USER.md` / Honcho user modeling).

## 2. Context & constraints

- **Existing environment (works today):**
  - Hostinger VPS `srv1608402.hstgr.cloud`, Debian 13 (trixie), **8 GB RAM**.
  - Docker Manager hosts two running projects: `hermes-agent-7qpk` (1 container) and
    `traefik` (1 container, reverse proxy + TLS).
  - Communication with Hermes-Agent via **WhatsApp**, already configured and working.
  - Hermes-Agent natively supports **connecting any MCP server** for extended capabilities.
- **Operator setup:** Windows laptop with Claude Desktop and Claude Code.
- **Cost target:** near-zero ongoing cost; no SaaS middlemen.
- **Ownership principle:** "own it outright" — data and compute stay on the user's VPS.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Hosting | **Self-host on the Hostinger VPS** (Docker, next to Hermes + Traefik) | Maximum ownership, no external account, data sits beside Hermes. |
| Capture interface | **WhatsApp → Hermes** (not Slack) | Already works; Hermes has an LLM and MCP support. Slack/Supabase-edge path from the article is unnecessary. |
| Summarization + keywords | **Done by Hermes' own LLM** | Hermes already analyzes messages; avoids a second LLM dependency. |
| Build vs. reuse | **Lean custom MCP server** | OB1's only deployment paths are **Supabase** (the SaaS middleman we're avoiding) or **Kubernetes** (too heavy for an 8 GB VPS already running Hermes + Traefik). Its gateway/MCP pieces are Supabase-edge-function-flavored. A small purpose-built Docker service is simpler to run and reason about while reusing OB1's primitives (pgvector, dedup, tool set). |
| Embeddings | **Local multilingual model on the VPS** (`multilingual-e5-small`) | No external API; handles the user's German + English mix; fits comfortably in 8 GB. |
| Retrieval scope (v1) | **WhatsApp + laptop (Claude Desktop / Claude Code)** | The "every AI plugs into one brain" payoff; requires authenticated remote MCP access. |
| Remote access | **Traefik subdomain + TLS + bearer token** | Traefik already runs and manages Let's Encrypt; minimal new surface. |

**Relationship to OB1:** we deliberately diverge from OB1's stack while keeping its good ideas.
OB1's base table is `thoughts`; ours is `captures` (purpose-built for this capture use case) — a
conscious divergence, not accidental. OB1 also ships importers (ChatGPT, Obsidian, X, Gmail,
Perplexity, …) and "skill packs" (e.g. **Auto-Capture**); we borrow the Auto-Capture idea for the
Hermes capture instruction (§4.1 D) and note the importers as a future migration option (§7).

## 4. Architecture

```
                          Hostinger VPS (Docker)
   ┌───────────────────────────────────────────────────────────────┐
   │                                                                 │
   │   ┌─────────────┐        internal docker network               │
   │   │  hermes-    │  MCP (HTTP, no TLS hop needed internally)     │
   │   │  agent      │ ───────────────────────────┐                 │
   │   └─────┬───────┘                             ▼                 │
   │         │ WhatsApp                    ┌────────────────┐         │
   │         │                             │ openbrain-mcp  │         │
   │         │                             │  - e5-small    │         │
   │         │                             │  - 6 MCP tools │         │
   │         │                             │  - dedup+auth  │         │
   │         │                             └───────┬────────┘         │
   │   ┌─────┴───────┐                             │ SQL              │
   │   │  traefik    │  TLS @ brain.<vps-domain>   ▼                  │
   │   │ (reverse    │ ──────────────────►  ┌────────────────┐        │
   │   │  proxy/TLS) │                      │ openbrain-db   │        │
   │   └─────┬───────┘                      │ Postgres 16 +  │        │
   │         │                              │ pgvector       │        │
   └─────────┼──────────────────────────────┴────────────────┴───────┘
             │ HTTPS + bearer token
             ▼
   ┌──────────────────────┐
   │  Laptop (Windows)    │
   │  Claude Desktop      │  MCP client → https://brain.<vps>/mcp
   │  Claude Code         │
   └──────────────────────┘

   WhatsApp ─▶ Hermes ─▶ [summarize + around 5 keywords] ─▶ openbrain-mcp.save ─▶ pgvector
   Any client ─▶ openbrain-mcp.search ─▶ pgvector top-k ─▶ results
```

### 4.1 Components

**A. `openbrain-db` (Postgres 16 + pgvector)**
- Single-purpose database for captured knowledge.
- Persistent Docker volume (survives container recreation).
- Reachable only on the internal Docker network (not published to the host/internet).

**B. `openbrain-mcp` (Python service)** — the only piece we write.
- Loads the embedding model **`intfloat/multilingual-e5-small`** in-process at startup
  (384-dim vectors; uses `query:` / `passage:` prefixes per the model's convention).
- Exposes an **MCP server over Streamable HTTP** with six tools (section 4.3).
- Generates embeddings, reads/writes Postgres, returns structured results.
- Authenticates every request via a **bearer token** (`OPENBRAIN_TOKEN`).
- Stateless except for the model; all persistence is in `openbrain-db`.

**C. `traefik` (existing)**
- New router rule: `brain.<vps-domain>` → `openbrain-mcp` with automatic Let's Encrypt TLS.
- Bearer token enforced by the MCP service (optionally also pre-checked at Traefik).
- Hermes → `openbrain-mcp` stays internal (no Traefik hop required).

**D. Hermes-Agent (existing, configured)**
- Add an **MCP server entry** pointing to `openbrain-mcp` on the internal network.
- Add a **capture instruction/skill** (based on OB1's **Auto-Capture** skill pack as a
  starting template): when the user sends content/links, Hermes fetches/uses the content,
  summarizes it, extracts around 5 keywords, and calls `openbrain.save(...)`, then confirms
  back on WhatsApp.

**E. Laptop clients (existing)**
- Claude Desktop and Claude Code each get an MCP entry pointing at
  `https://brain.<vps-domain>/mcp` with the bearer token.

### 4.2 Data model

Table `captures`:

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | generated |
| `raw_text` | `text` | original/source content (or pasted text) |
| `summary` | `text` | Hermes-generated summary |
| `keywords` | `text[]` | around 5 keywords |
| `source` | `text` | e.g. `youtube`, `substack`, `other` |
| `source_url` | `text` null | original link if available |
| `lang` | `text` null | detected/declared language |
| `metadata` | `jsonb` | extensible (people, topics, action items, etc.) |
| `fingerprint` | `text` unique | dedup key: SHA-256 of the normalized `source_url` (else of `raw_text`) |
| `embedding` | `vector(384)` | embedding of `summary` (+ keywords) |
| `created_at` | `timestamptz` | default `now()` |
| `updated_at` | `timestamptz` | default `now()`; bumped by `update` |

- Index: `ivfflat` (or `hnsw`) on `embedding` using cosine distance.
- Index: unique on `fingerprint` (enables dedup / idempotent capture; borrowed from OB1's
  "fingerprint dedup" recipe).
- We embed the **summary** (concise, meaning-dense). Raw text is retained for reference and
  future re-embedding (e.g. if upgrading to `bge-m3`).

### 4.3 MCP tools

1. **`save`** — `{ raw_text, summary, keywords[~5], source, source_url?, lang?, metadata? }`
   → computes `fingerprint`; if a row with that fingerprint exists, returns the existing
   `{ id, deduped: true }` **without** re-embedding; otherwise embeds summary, inserts row,
   returns `{ id, stored: true }`. (Idempotent — resending the same link is a no-op.)
2. **`search`** — `{ query, k? (default 5) }`
   → embeds query, returns top-k by cosine similarity with summary, keywords, source, score.
3. **`list_recent`** — `{ n? (default 10) }` → most recently captured items.
4. **`stats`** — total count, counts by source, date range, top keywords.
5. **`delete`** — `{ id }` → removes the capture with that id; returns `{ id, deleted: bool }`.
   (Prune mis-captures; borrowed from OB1's `delete-thought-mcp`.)
6. **`update`** — `{ id, summary?, keywords?, metadata? }` → updates the given fields,
   re-embeds if `summary` changed, bumps `updated_at`; returns `{ id, updated: true }`.
   (Borrowed from OB1's `update-thought-mcp`.)

## 5. End-to-end flows

### 5.1 Capture
1. User sends a YouTube/Substack link (or text) to Hermes via WhatsApp.
2. Hermes obtains the content (see open question 8.1), summarizes it, and extracts keywords.
3. Hermes calls `openbrain.save` with raw text, summary, keywords, source, url.
4. `openbrain-mcp` computes the fingerprint; if it's a duplicate it returns the existing id
   (`deduped`), otherwise it embeds the summary and stores the row in `openbrain-db`.
5. Hermes replies on WhatsApp confirming what was captured (title/summary + keywords), or that
   the item was already stored.

### 5.2 Retrieval — laptop
1. In Claude Desktop or Claude Code, user asks a question.
2. The client calls `openbrain.search` over HTTPS (bearer token) via `brain.<vps>`.
3. `openbrain-mcp` embeds the query, runs pgvector top-k, returns matches.

### 5.3 Retrieval — WhatsApp
1. User asks Hermes to recall something.
2. Hermes calls `openbrain.search` on the internal network and replies on WhatsApp.

## 6. Security model

- **Transport:** TLS terminated at Traefik (Let's Encrypt) for all laptop/internet traffic.
- **AuthN:** static **bearer token** (`OPENBRAIN_TOKEN`), required on every MCP request;
  stored in laptop client configs and validated by `openbrain-mcp`.
- **Network:** `openbrain-db` is never published; only `openbrain-mcp` is reachable, and only
  via Traefik (externally) or the internal Docker network (Hermes).
- **Secrets:** token and DB password in environment/`.env`, not committed.
- **Optional hardening (post-v1):** IP allow-list at Traefik, token rotation, read-only token
  for laptop vs. write-capable token for Hermes.

## 7. Non-goals (YAGNI for v1)

- No Slack capture path, no Supabase, no edge functions.
- No web dashboard / visualization UI.
- No automated daily digest or weekly-review automation (can be added later via the same MCP).
- No multi-user support — single owner.
- No migration of existing notes for v1. Later options: a one-off `save` script, or adapt one of
  OB1's importers (ChatGPT, Obsidian, X, Gmail, Perplexity, …) to write into `captures`.
- No schema-aware LLM routing / multiple metadata schemas (OB1 has this; we keep a single
  `captures` table with a flexible `metadata` jsonb instead).

## 8. Open questions / risks to resolve during planning

1. **Content fetching:** Does Hermes have a tool to fetch a YouTube transcript / Substack
   article body from a link? If yes, capture is fully hands-off. If no, fallback options:
   (a) add/enable a fetch tool in Hermes, or (b) user pastes the text. **To verify first.**
2. **MCP transport compatibility:** Confirm Hermes' MCP client and Claude Desktop/Code all
   support Streamable HTTP with a bearer header (else fall back to SSE or an SSH tunnel for
   the laptop).
3. **VPS domain for `brain.` subdomain:** confirm DNS control for a subdomain under
   `srv1608402.hstgr.cloud` (or use an existing owned domain) so Traefik can issue TLS.
4. **Embedding prefixes:** ensure `save` uses `passage:` and `search` uses `query:` prefixes
   required by the e5 family for correct similarity.
5. **Resource check at deploy:** confirm headroom with Hermes running (target: model +
   Postgres comfortably under total 8 GB).

## 9. Success criteria

- Sending a YouTube or Substack link via WhatsApp results, within a few seconds, in a stored
  capture (verifiable via `list_recent` / `stats`) with a summary and around 5 keywords.
- A semantic query from Claude Desktop **and** Claude Code returns the relevant capture even
  when the query wording differs from the stored text (incl. across German/English).
- The same query asked through WhatsApp/Hermes returns the same capture.
- Sending the **same link twice** does not create a duplicate row (fingerprint dedup); the
  second `save` reports `deduped`.
- `openbrain-db` data survives a container restart.
- No component depends on an external paid SaaS; ongoing cost ≈ €0.

## 10. Implementation outline (to be expanded into a plan)

1. Provision `openbrain-db` (Postgres 16 + pgvector) container + volume; create schema
   (incl. unique `fingerprint`) + indexes.
2. Build `openbrain-mcp` service (model load, embed, fingerprint dedup, 6 MCP tools —
   save/search/list_recent/stats/delete/update, bearer auth); Dockerfile.
3. Compose both into the existing Docker environment; verify internal connectivity.
4. Add Traefik router + TLS for `brain.<vps>`; verify HTTPS + token from laptop.
5. Register `openbrain-mcp` as an MCP server in Hermes; add the capture instruction/skill.
6. Configure Claude Desktop + Claude Code MCP clients on the laptop.
7. End-to-end test: capture via WhatsApp, retrieve from all three surfaces; verify persistence.
8. (Optional) seed/migration of existing notes; hardening pass.
