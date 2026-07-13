# openbrain-mcp

Self-hosted MCP server for OpenBrain memory. Tools: `save`, `search`, `list_recent`, `stats`,
`delete`, `update`. Embeds with `intfloat/multilingual-e5-small`; stores in Postgres + pgvector.

Env: `DATABASE_URL`, `OPENBRAIN_TOKEN`, optional `OPENBRAIN_MODEL`.
Run: `python -m app.server` (serves MCP at `/mcp`, health at `/health`, port 8080).
