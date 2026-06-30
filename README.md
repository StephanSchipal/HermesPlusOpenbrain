# HermesPlusOpenbrain

Adding an [OpenBrain](https://github.com/RadixSeven/OpenBrain)-style, self-owned, agent-readable
**secondary memory** to a self-hosted **Hermes-Agent** deployment.

**Use case:** send information (YouTube, Substack, …) to Hermes-Agent via WhatsApp →
it analyzes, summarizes, and extracts ~5 keywords → stores it in a self-hosted semantic
database on the same VPS → searchable by meaning from WhatsApp (via Hermes) and from the
laptop (Claude Desktop / Claude Code) over MCP.

## Status

- [x] Design spec — [`docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md`](docs/superpowers/specs/2026-06-30-hermes-openbrain-memory-design.md)
- [ ] Implementation plan
- [ ] Implementation

## Architecture (summary)

Two new Docker containers alongside the existing `hermes-agent` + `traefik`:

- **`openbrain-db`** — Postgres 16 + `pgvector` (self-hosted, owned, persistent).
- **`openbrain-mcp`** — small Python service: local multilingual embeddings
  (`multilingual-e5-small`) + an MCP server exposing `save` / `search` / `list_recent` / `stats`,
  protected by a bearer token, fronted by Traefik (TLS) for remote laptop access.

See the design spec for the full architecture, data model, flows, security model, and open
questions.
