# Hermes-Agent + OpenBrain Secondary Memory — Design Spec

**Date:** 2026-06-30
**Author:** Stephan (with Claude Code, brainstorming session)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Add an [OpenBrain](https://github.com/RadixSeven/OpenBrain)-style, self-owned, agent-readable
**secondary memory** to an existing, working **Hermes-Agent** deployment.

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
| Build vs. reuse | **Lean custom MCP server** | RadixSeven fork is Supabase/edge-function bound; bending it is more work than a small purpose-built service. |
| Embeddings | **Local multilingual model on the VPS** (`multilingual-e5-small`) | No external API; handles the user's German + English mix; fits comfortably in 8 GB. |
| Retrieval scope (v1) | **WhatsApp + laptop (Claude Desktop / Claude Code)** | The "every AI plugs into one brain" payoff; requires authenticated remote MCP access. |
| Remote access | **Traefik subdomain + TLS + bearer token** | Traefik already runs and manages Let's Encrypt; minimal new surface. |

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
   │         │                             │  - save/search │         │
   │         │                             │  - bearer auth │         │
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
- Exposes an **MCP server over Streamable HTTP** with four tools (section 4.3).
- Generates embeddings, reads/writes Postgres, returns structured results.
- Authenticates every request via a **bearer token** (`OPENBRAIN_TOKEN`).
- Stateless except for the model; all persistence is in `openbrain-db`.

**C. `traefik` (existing)**
- New router rule: `brain.<vps-domain>` → `openbrain-mcp` with automatic Let's Encrypt TLS.
- Bearer token enforced by the MCP service (optionally also pre-checked at Traefik).
- Hermes → `openbrain-mcp` stays internal (no Traefik hop required).

**D. Hermes-Agent (existing, configured)**
- Add an **MCP server entry** pointing to `openbrain-mcp` on the internal network.
- Add a **capture instruction/skill**: when the user sends content/links, Hermes
  fetches/uses the content, summarizes it, extracts around 5 keywords, and calls
  `openbrain.save(...)`, then confirms back on WhatsApp.

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
| `embedding` | `vector(384)` | embedding of `summary` (+ keywords) |
| `created_at` | `timestamptz` | default `now()` |

- Index: `ivfflat` (or `hnsw`) on `embedding` using cosine distance.
- We embed the **summary** (concise, meaning-dense). Raw text is retained for reference and
  future re-embedding (e.g. if upgrading to `bge-m3`).

### 4.3 MCP tools

1. **`save`** — `{ raw_text, summary, keywords[~5], source, source_url?, lang?, metadata? }`
   → embeds summary, inserts row, returns `{ id, stored: true }`.
2. **`search`** — `{ query, k? (default 5) }`
   → embeds query, returns top-k by cosine similarity with summary, keywords, source, score.
3. **`list_recent`** — `{ n? (default 10) }` → most recently captured items.
4. **`stats`** — total count, counts by source, date range, top keywords.

## 5. End-to-end flows

### 5.1 Capture
1. User sends a YouTube/Substack link (or text) to Hermes via WhatsApp.
2. Hermes obtains the content (see open question 8.1), summarizes it, and extracts keywords.
3. Hermes calls `openbrain.save` with raw text, summary, keywords, source, url.
4. `openbrain-mcp` embeds the summary and stores the row in `openbrain-db`.
5. Hermes replies on WhatsApp confirming what was captured (title/summary + keywords).

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
- No migration of existing notes (can be done later with a one-off `save` script).

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
- `openbrain-db` data survives a container restart.
- No component depends on an external paid SaaS; ongoing cost ≈ €0.

## 10. Implementation outline (to be expanded into a plan)

1. Provision `openbrain-db` (Postgres 16 + pgvector) container + volume; create schema + index.
2. Build `openbrain-mcp` service (model load, embed, 4 MCP tools, bearer auth); Dockerfile.
3. Compose both into the existing Docker environment; verify internal connectivity.
4. Add Traefik router + TLS for `brain.<vps>`; verify HTTPS + token from laptop.
5. Register `openbrain-mcp` as an MCP server in Hermes; add the capture instruction/skill.
6. Configure Claude Desktop + Claude Code MCP clients on the laptop.
7. End-to-end test: capture via WhatsApp, retrieve from all three surfaces; verify persistence.
8. (Optional) seed/migration of existing notes; hardening pass.
