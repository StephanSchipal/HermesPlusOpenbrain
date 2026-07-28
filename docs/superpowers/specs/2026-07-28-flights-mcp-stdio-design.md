# flights-mcp on Hermes — Native stdio Registration — Design Spec

**Date:** 2026-07-28
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-28)
**Status:** Approved design — ready for implementation planning

Supersedes `2026-07-28-flights-mcp-deployment-design.md` (Docker/HTTP-wrapper approach), which was
based on the incorrect assumption that Hermes only supports streamable-HTTP MCP servers.

---

## 1. Goal

Give Hermes (WhatsApp agent) flight-search capability via Duffel, registered as a native `stdio`
MCP server — no new container, no vendored code, no network wiring.

## 2. Context & correction

Hermes' MCP dashboard ("Add MCP Server") offers a `stdio` transport (`Command`/`Args`/
`Environment` fields), and its own Catalog already runs servers this way — e.g. `blender`'s entry
runs `uvx blender-mcp==1.6.4` directly. This means Hermes spawns MCP servers as subprocesses
inside its own container and already has `uv`/`uvx` on `PATH` with outbound internet access
(needed to fetch packages from PyPI or, per the `n8n` catalog entry's "Installs from: github...",
directly from git). This directly contradicts what prior research concluded (streamable-HTTP
only) — that research was wrong.

`flights-mcp` isn't published to PyPI, but `uv`/`uvx` can run a console-script entry point straight
from a git repository via `--from git+<url>[@<ref>]`. Verified locally:

```
DUFFEL_API_KEY_LIVE=duffel_test_placeholder uvx --from git+https://github.com/ravinahp/flights-mcp.git flights-mcp
```

cloned the repo, built the package (hatchling, per its `pyproject.toml`), installed 32
dependencies, and logged `Starting Find Flights MCP server` / `Server initialized successfully`
before hitting an unrelated harmless error caused by feeding it `/dev/null` as stdin in the test
(a real MCP client keeps the pipe open). The resolved commit was
`749d7ad14cce86cd1ecd2c236c30b272f23f2d1e`.

## 3. Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Deployment shape | Register directly with Hermes as `stdio`, no repo changes at all | Hermes already runs other servers this way (§2); no container/wrapper/vendoring needed |
| Version pinning | Pin to the tested commit (`git+https://github.com/ravinahp/flights-mcp.git@749d7ad14cce86cd1ecd2c236c30b272f23f2d1e`), not a floating `HEAD` | Matches the Catalog's own convention (`blender-mcp==1.6.4` is pinned); avoids an upstream change silently breaking the tool later |
| Duffel key | Test key to start, same `DUFFEL_API_KEY_LIVE` env var name for both tiers | Unchanged from the original brainstorming answer; going live later is just swapping the env value in Hermes' MCP entry (§7) — no redeploy |
| Registration mechanism | Hermes dashboard's "Add MCP Server" form | User-facing, no VPS shell access needed for this step; persistence verified explicitly in testing (§6) rather than assumed |

## 4. Architecture

```
  WhatsApp user
      │
      ▼
  Hermes-Agent container (existing)
      │  spawns subprocess (stdio, stdin/stdout pipe — no network hop)
      ▼
  uvx --from git+https://github.com/ravinahp/flights-mcp.git@<pinned-sha> flights-mcp
      │  outbound HTTPS
      ▼
  Duffel API (api.duffel.com), test key -> simulated offers
```

No new Docker service, no new Docker network membership, nothing added to this repo.

## 5. Registration details

Via Hermes dashboard → MCP → "Add MCP Server":

- **Name:** `flights-mcp`
- **Transport:** `stdio`
- **Command:** `uvx`
- **Args:** `--from git+https://github.com/ravinahp/flights-mcp.git@749d7ad14cce86cd1ecd2c236c30b272f23f2d1e flights-mcp`
- **Environment:** `DUFFEL_API_KEY_LIVE=<your test key>`

(If the dashboard's Args field wants one arg per line rather than a single string, split
accordingly: `--from`, `git+https://github.com/ravinahp/flights-mcp.git@749d7ad14cce86cd1ecd2c236c30b272f23f2d1e`,
`flights-mcp`.)

## 6. Persistence check (carried over caution from Task 5.1)

When `openbrain-mcp` was registered, a direct `config.yaml` edit did **not** survive a Hermes
container restart — only `hermes mcp configure` (CLI) persisted correctly. It's unconfirmed
whether this dashboard form uses the same underlying persisted store or something more fragile.
This design requires an explicit restart test (§8) before treating the registration as done —
if the dashboard entry doesn't survive a restart, fall back to `hermes mcp configure` (CLI) with
the same Command/Args/Environment values.

## 7. Going live later (non-goal now, documented for later)

Duffel verification (email → company info → payment info → agreement) produces a `duffel_live_...`
key from **More → Developer → Create Live Token**. Swap the `DUFFEL_API_KEY_LIVE` value in the
existing Hermes MCP entry — same variable name, no Command/Args change, no code anywhere in this
repo to touch. `flights-mcp` stays read-only regardless of key tier (search only, never
books/charges).

## 8. Testing

1. Register via the dashboard (§5).
2. `hermes mcp catalog` (or the dashboard's server list) shows `flights-mcp` with its three tools
   (`search_flights`, `get_offer_details`, `search_multi_city`).
3. **Restart test:** restart the Hermes container, re-check the catalog/dashboard — entry must
   still be present with the same Command/Args/Environment (§6). If it's gone, redo registration
   via `hermes mcp configure` instead.
4. End-to-end: ask Hermes a real flight-search question over WhatsApp, confirm a reply with
   simulated (test-key) flight offers — sane airline/times/price/stops shape.

## 9. Non-goals

- No PyPI publication of `flights-mcp` — running straight from git is sufficient.
- No Catalog submission (the curated, "Nous-approved" list) — this is a personal "Add MCP Server"
  entry, not a shared catalog contribution.
- No live Duffel key yet (§7 documents the swap for whenever that happens).
- No repo changes of any kind — this whole feature lives in Hermes' own configuration.

## 10. Success criteria

- `flights-mcp` shows as registered and reachable in Hermes after both initial registration and a
  container restart.
- A real WhatsApp flight-search question returns a sane simulated offer.
- Duffel key upgrade path (§7) requires no repo change, confirmed by design (not just assumed).

## 11. Implementation outline

1. Register `flights-mcp` in Hermes' dashboard per §5.
2. Verify catalog listing + tool set.
3. Restart Hermes container, re-verify persistence (§6) — fall back to `hermes mcp configure` CLI
   if the dashboard entry didn't survive.
4. End-to-end WhatsApp test (§8.4).
5. Update `README.md` to document the new capability.
