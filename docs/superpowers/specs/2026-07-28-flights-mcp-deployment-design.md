# flights-mcp on Hostinger — Streamable-HTTP Deployment Design

**Date:** 2026-07-28
**Author:** Stephan (with Claude Code, brainstorming session 2026-07-28)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Give Hermes (the WhatsApp agent) the ability to search flights, by deploying
[`flights-mcp`](https://github.com/ravinahp/flights-mcp) (MIT-licensed, Duffel-API-backed) as a
streamable-HTTP MCP service on the Hostinger VPS, reachable only from Hermes-Agent's own Docker
network, and registered with Hermes the same way `openbrain-mcp` was (Task 5.1 of the OpenBrain
plan).

## 2. Context & constraints

- Hermes-Agent registers MCP servers **only** over streamable-HTTP (`hermes mcp configure`) — it
  has no stdio support. `flights-mcp` upstream is **hardcoded to stdio**
  (`mcp.run(transport='stdio')` in `src/flights/server.py`), so a straight "add the repo" is not
  possible; a small HTTP wrapper is required.
- `flights-mcp` uses the same `mcp.server.fastmcp.FastMCP` SDK as our own `openbrain-mcp`, so the
  wrapper can mirror `openbrain-mcp/app/server.py`'s proven pattern almost line for line:
  `BearerAuthMiddleware` (Starlette `BaseHTTPMiddleware`, `/health` exempted) +
  `mcp.streamable_http_app()` + `uvicorn.run(host="0.0.0.0", port=...)`.
- **Host-header gotcha, already solved once:** `openbrain-mcp/app/server.py` constructs its
  `FastMCP` with `host="0.0.0.0"` specifically because the SDK's default (`127.0.0.1`) triggers a
  DNS-rebinding host-header check that 421s any request whose `Host` header isn't a localhost
  variant — which is exactly what happens once Hermes calls the container by its Docker service
  name. `flights-mcp`'s `src/flights/services/search.py` currently does `FastMCP("find-flights-mcp")`
  with **no** `host` argument, so it will hit the identical 421 bug once deployed behind a
  container-name URL. Since we're vendoring the source anyway, this one-line constructor change
  is part of this design, not a follow-up fix.
- `flights-mcp` is **read-only** — `search_flights`, `search_multi_city`, `get_offer_details` only.
  It cannot book, and per Duffel's own docs a test key (`duffel_test`) never touches real payment
  rails regardless. Per your answers: you're starting with a **Duffel test key** (simulated data)
  and want the service **internal-only** — no Traefik vhost, no public domain, matching how
  `openbrain-db` is kept off `hermes_net` today (only reachable by services on the same Docker
  network as Hermes).
- `flight_client = DuffelClient(logger)` in `services/search.py` runs **at import time**, and
  `DuffelClient.__init__` calls `get_api_token()` immediately — so `DUFFEL_API_KEY_LIVE` must
  already be set in the container's environment before the process starts (a plain Docker Compose
  `environment:` entry satisfies this; no code change needed).
- The repo doesn't vendor third-party MCP servers yet — `openbrain-mcp` is our own code.
  `flights-mcp` will be the first vendored dependency, copied in under its own top-level directory
  (MIT license permits this; `LICENSE` file carried over unmodified).

## 3. Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Transport | Vendor `flights-mcp`'s source into this repo, add a new `http_server.py` module that imports its existing `mcp` `FastMCP` instance and serves it over streamable-HTTP | Mirrors the exact `openbrain-mcp` pattern already proven in production; upstream's tool logic (`search_flights` etc.) is untouched, only the transport is added |
| `FastMCP` host binding | Vendored `services/search.py`: add `host="0.0.0.0"` to the existing `FastMCP("find-flights-mcp")` call | Avoids the same 421 DNS-rebinding bug `openbrain-mcp` already hit and documented (§2) |
| Auth | Bearer token middleware, identical shape to `openbrain-mcp`'s `BearerAuthMiddleware`, new `FLIGHTS_MCP_TOKEN` secret | `hermes mcp configure` expects a bearer header regardless of network exposure; costs nothing to add and keeps the two services consistent |
| Network exposure | **Internal-only** — joins `hermes_net` (`hermes-agent-7qpk_default`) only, no Traefik labels, no public host/domain | Your explicit choice; smaller attack surface, no cert/DNS to manage for a service nothing outside Hermes needs to call |
| Duffel key | Test key (`duffel_test...`) to start | Your explicit choice; simulated data is enough to validate the whole pipeline (Hermes → flights-mcp → Duffel → WhatsApp reply) before deciding whether to go through Duffel's live-key verification later |
| Docker Compose file | New service block appended to the existing `deploy/docker-compose.openbrain.yml` | One more small internal service; a dedicated compose file would only add an extra `docker compose -f ...` invocation to every deploy step for no isolation benefit — nothing in this service depends on or is depended on by the openbrain services, but they already share the same VPS/network conventions and `.env` |
| Image build | Single-stage `python:3.11-slim` + `pip install .`, matching `openbrain-mcp/Dockerfile`'s style | `flights-mcp`'s dependencies (`httpx`, `pydantic`, `python-dotenv`, `mcp`) are light — no ML wheels like `openbrain-mcp`'s `sentence-transformers`, so the upstream two-stage `uv` build (which also has an unresolved `--chown=app:app` referencing a user that image never creates) buys nothing here |

## 4. Architecture

```
  WhatsApp user
      │
      ▼
  Hermes-Agent (Docker, existing)
      │  streamable-HTTP, Bearer auth
      │  http://flights-mcp:8081/mcp   (hermes_net, container-name DNS)
      ▼
  ┌─────────────────────────────────────┐
  │ flights-mcp container (NEW)          │
  │  app/http_server.py (NEW, ours)      │  BearerAuthMiddleware, /health,
  │   └─ imports mcp from                │  streamable_http_app(), uvicorn:8081
  │      flights.services.search         │
  │  flights.services.search (vendored,  │  search_flights / get_offer_details /
  │   host="0.0.0.0" added)              │  search_multi_city (unchanged logic)
  │  flights.api.client.DuffelClient     │
  └─────────────────────────────────────┘
      │  HTTPS, outbound only
      ▼
  Duffel API (api.duffel.com)
```

Only one Docker network touched (`hermes_net`) — no new network, no new volume (the service is
stateless).

## 5. Components

### 5.1 New vendored directory: `flights-mcp/`

Top-level sibling to `openbrain-mcp/`, structured to match its `src`-layout upstream
(`pyproject.toml` already declares `packages = ["src/flights"]`, installs as the top-level
`flights` package):

```
flights-mcp/
├── Dockerfile                       (NEW, ours — single-stage, see §3)
├── LICENSE                          (copied from upstream, unmodified)
├── README.md                        (short, ours — deployment-focused, not upstream's Claude-Desktop guide)
├── pyproject.toml                   (copied from upstream, unmodified)
└── src/flights/
    ├── __init__.py                  (copied, unmodified)
    ├── server.py                    (copied, unmodified — still the stdio entrypoint, kept for reference/local testing; NOT used by the Docker image)
    ├── http_server.py               (NEW, ours — see below)
    ├── api/                         (copied, unmodified)
    ├── config/                      (copied, unmodified)
    └── models/                      (copied, unmodified)
```

`src/flights/http_server.py` (new, mirrors `openbrain-mcp/app/server.py`'s bottom half):

```python
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .services.search import mcp  # noqa: F401 — registers the @mcp.tool() functions
from .config.api import get_api_token  # fail fast if DUFFEL_API_KEY_LIVE is unset

import os
FLIGHTS_MCP_TOKEN = os.environ.get("FLIGHTS_MCP_TOKEN", "")

class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        expected = f"Bearer {FLIGHTS_MCP_TOKEN}"
        if not FLIGHTS_MCP_TOKEN or request.headers.get("authorization") != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})

def build_app() -> Starlette:
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.add_middleware(BearerAuthMiddleware)
    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=8081)
```

`src/flights/services/search.py` — one-line change from upstream: `mcp = FastMCP("find-flights-mcp")`
becomes `mcp = FastMCP("find-flights-mcp", host="0.0.0.0")`, with the same explanatory comment
`openbrain-mcp/app/server.py` already carries (§2), so a future upstream sync doesn't silently
drop it.

`flights-mcp/Dockerfile` (new, mirrors `openbrain-mcp/Dockerfile`'s shape, no model pre-download
step needed):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN mkdir -p src/flights && touch src/flights/__init__.py && \
    python -m pip install --no-cache-dir . && \
    rm -rf src
COPY src ./src
EXPOSE 8081
CMD ["python", "-m", "flights.http_server"]
```

### 5.2 `deploy/docker-compose.openbrain.yml` — new service block

```yaml
  flights-mcp:
    build: ../flights-mcp
    restart: unless-stopped
    environment:
      DUFFEL_API_KEY_LIVE: ${DUFFEL_API_KEY_LIVE}
      FLIGHTS_MCP_TOKEN: ${FLIGHTS_MCP_TOKEN}
    networks:
      - hermes_net   # reachable by Hermes as `flights-mcp:8081`; no other network
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8081/health')\""]
      interval: 30s
      timeout: 5s
      retries: 3
```

No Traefik labels (internal-only, §3), no volumes (stateless), no `openbrain_internal` network
membership (it never talks to `openbrain-db`).

### 5.3 `deploy/.env` — new variables

- `DUFFEL_API_KEY_LIVE` — the Duffel test key (env var name is fixed by upstream regardless of
  test/live tier, per `src/flights/config/api.py`).
- `FLIGHTS_MCP_TOKEN` — new bearer secret, generated the same way as `OPENBRAIN_TOKEN`
  (`openssl rand -hex 32`), used both by `hermes mcp configure`'s auth header and by
  `BearerAuthMiddleware`.

### 5.4 Hermes registration (VPS-side, user-run — same pattern as Task 5.1)

Once the container is built and healthy: `hermes mcp configure` inside the live Hermes container,
pointing at `http://flights-mcp:8081/mcp` with `Authorization: Bearer <FLIGHTS_MCP_TOKEN>`,
verified via `hermes mcp catalog`.

## 6. Data flow

1. WhatsApp user asks Hermes about flights (e.g. "find me a one-way flight VIE→LHR next Tuesday").
2. Hermes calls the `search_flights` tool over its registered streamable-HTTP connection to
   `flights-mcp:8081/mcp`, bearer-authenticated.
3. `BearerAuthMiddleware` validates the header, `/mcp` request reaches the unmodified
   `search_flights` tool.
4. The tool builds Duffel slices and calls `DuffelClient.create_offer_request` → outbound HTTPS to
   `api.duffel.com` using the test key → simulated offers come back.
5. The tool formats and returns JSON (offer id, price, slices, carrier, stops) as the tool result;
   Hermes turns it into a WhatsApp reply.
6. `get_offer_details` / `search_multi_city` follow the same path for their respective use cases.

## 7. Error handling

- Missing/invalid `Authorization` header → `401 {"error": "unauthorized"}` from
  `BearerAuthMiddleware`, before any tool code runs (identical to `openbrain-mcp`).
- `DUFFEL_API_KEY_LIVE` unset at container start → `get_api_token()` raises immediately at import
  time (`services/search.py` module load), so the container fails fast and the healthcheck never
  goes green — surfaced as an obvious `docker compose ps` failure rather than a silent 500 on first
  use.
- Duffel API errors (bad airport code, supplier timeout, network failure) — unchanged upstream
  behavior: `search.py`'s tool functions catch, `logger.error(..., exc_info=True)`, then
  `raise`, which the MCP SDK surfaces to Hermes as a tool-call error. Out of scope for this design
  to change; if Hermes's handling of tool errors needs improving, that's a separate piece of work
  once we see it happen with real usage.
- `/health` is unauthenticated by design (matches `openbrain-mcp`), used only by Docker's
  healthcheck inside the internal network.

## 8. Testing

`flights-mcp` upstream ships one test file, `tests/test_duffel_api.py`, which appears to hit the
real Duffel API rather than using mocks — not wired into any CI here and out of scope to adopt.
Verification for this deployment is manual, mirroring how `openbrain-mcp` was verified on the VPS
(Task 4.1/4.2, 5.1/5.2):

1. **Local build:** `docker compose -f deploy/docker-compose.openbrain.yml build flights-mcp` — build
   succeeds, `python -m flights.http_server` starts without the `DUFFEL_API_KEY_LIVE` import-time
   error (§7).
2. **Local run + health:** bring the service up locally with a test `.env`, `curl -H "Authorization: Bearer $FLIGHTS_MCP_TOKEN" http://localhost:8081/health` → `{"ok": true}`; without the header →
   `401`.
3. **Local tool smoke test:** an MCP client (or `npx @modelcontextprotocol/inspector`) calls
   `search_flights` with a simple one-way query against the test key, confirms a JSON offer list
   comes back (simulated data, per Duffel's test mode).
4. **VPS deploy:** same build/up on the Hostinger VPS, `docker compose ps` shows `flights-mcp`
   healthy, `docker exec` a curl from inside the Hermes container to
   `http://flights-mcp:8081/health` to confirm same-network reachability (mirrors Task 4.1's
   verification style, but internal instead of public-HTTPS since there's no Traefik hop here).
5. **Hermes registration:** `hermes mcp configure` + `hermes mcp catalog` (Task 5.1 pattern).
6. **End-to-end WhatsApp test:** ask Hermes a real flight-search question from WhatsApp, confirm a
   sensible (simulated) reply comes back, mirroring Task 5.2's acceptance style.

## 9. Deployment

New service, no impact on existing `openbrain-db`/`openbrain-mcp`/`openbrain-gui` — they don't
share a network or depend on `flights-mcp` in either direction. Rollout is additive: build and
start `flights-mcp`, verify per §8, then run `hermes mcp configure` once it's healthy. No downtime
risk to the existing OpenBrain stack.

## 10. Non-goals (this iteration)

- No booking capability — `flights-mcp` is read-only by design; not adding write/booking tools.
- No public/HTTPS exposure, no Traefik vhost, no domain — internal-only per your explicit choice
  (§3). Can be revisited later the same way `openbrain-mcp` already has both internal and public
  paths, if you ever want to query it directly from Claude Desktop/iPhone.
- No live Duffel key / real booking data yet — starting on the test key; upgrading is just an
  `.env` value swap plus going through Duffel's verification flow whenever you decide to.
- No changes to `flights-mcp`'s tool logic, response formatting, or error-handling behavior beyond
  the one `host="0.0.0.0"` constructor fix required for correct operation (§2) — this is a
  transport-only deployment, not a fork/feature project.
- No automated test suite added for `flights-mcp` in this repo (mirrors upstream's own
  live-API-only test, §8) — manual verification only, same bar as `openbrain-mcp`'s early phases.

## 11. Success criteria

- `docker compose -f deploy/docker-compose.openbrain.yml up -d flights-mcp` on the VPS results in
  a healthy container (`docker compose ps` shows `healthy`).
- `flights-mcp:8081/health` is reachable from inside the Hermes container but **not** from the
  public internet (no Traefik route exists for it).
- `hermes mcp catalog` lists `flights-mcp` as registered and reachable.
- A real WhatsApp message asking for a flight search returns a Hermes reply containing simulated
  flight offers (test-key data) with sane prices/routes/times.
- No regression to `openbrain-db`/`openbrain-mcp`/`openbrain-gui` — unaffected by this change.

## 12. Implementation outline (to be expanded into a plan)

1. Vendor `flights-mcp` source into `flights-mcp/` (copy `src/`, `pyproject.toml`, `LICENSE` from
   the local clone at `C:\Users\steve\mcp-servers\flights-mcp`).
2. Apply the `host="0.0.0.0"` fix to `src/flights/services/search.py`'s `FastMCP(...)` call.
3. Write `src/flights/http_server.py` (§5.1).
4. Write `flights-mcp/Dockerfile` (§5.1) and a short deployment-focused `flights-mcp/README.md`.
5. Add the `flights-mcp` service block to `deploy/docker-compose.openbrain.yml` (§5.2); add
   `DUFFEL_API_KEY_LIVE`/`FLIGHTS_MCP_TOKEN` to `deploy/.env.example` (§5.3).
6. Local build + health/auth/tool smoke test (§8 steps 1-3).
7. `[VPS]` build + start on Hostinger, healthcheck verification (§8 step 4).
8. `[VPS]` `hermes mcp configure` + `hermes mcp catalog` verification (§8 step 5).
9. `[VPS]` end-to-end WhatsApp flight-search test (§8 step 6).
10. Update `README.md` with the new capability, mirroring how Task 5.1/5.2 were documented for
    `openbrain-mcp`.
