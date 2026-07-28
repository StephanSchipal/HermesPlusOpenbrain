# flights-mcp on Hostinger — Streamable-HTTP Deployment Implementation Plan

> **SUPERSEDED 2026-07-28** — see `2026-07-28-flights-mcp-stdio-design.md` and its plan. Hermes
> supports `stdio`-transport MCP servers directly (confirmed via its dashboard's Catalog, e.g.
> `uvx blender-mcp==1.6.4`), making this whole Docker/HTTP-wrapper approach unnecessary. No tasks
> below were executed — the worktree/branch created for this plan was removed unused.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy `flights-mcp` (Duffel-API flight search) as a streamable-HTTP MCP service on the
Hostinger VPS, internal-only on Hermes-Agent's Docker network, so Hermes/WhatsApp can search
flights.

**Architecture:** Vendor the MIT-licensed `flights-mcp` source into a new top-level `flights-mcp/`
directory, add a thin `http_server.py` wrapper (Starlette + bearer auth + `/health`, mirroring
`openbrain-mcp/app/server.py`), fix a host-binding bug the vendored code would otherwise hit
behind a container-name URL, containerize it, and add it as a new internal-only service in
`deploy/docker-compose.openbrain.yml`.

**Tech Stack:** Python 3.11, `mcp` SDK (`FastMCP`/`streamable_http_app`), Starlette, uvicorn,
Docker Compose, Duffel API (test key).

**Spec:** `docs/superpowers/specs/2026-07-28-flights-mcp-deployment-design.md`

---

## File Structure

```
flights-mcp/                              (NEW top-level dir, vendored + ours)
├── Dockerfile                            (NEW, ours)
├── LICENSE                               (copied from upstream, unmodified)
├── README.md                             (NEW, ours — short, deployment-focused)
├── pyproject.toml                        (copied from upstream, unmodified)
└── src/flights/
    ├── __init__.py                       (copied, unmodified)
    ├── server.py                         (copied, unmodified — stdio entrypoint, unused by Docker image)
    ├── http_server.py                    (NEW, ours)
    ├── api/                              (copied, unmodified)
    ├── config/                           (copied, unmodified)
    ├── models/                           (copied, unmodified)
    └── services/
        └── search.py                     (copied, ONE line modified — host="0.0.0.0")

deploy/docker-compose.openbrain.yml       (MODIFIED — new flights-mcp service block)
deploy/.env.example                       (MODIFIED — new DUFFEL_API_KEY_LIVE, FLIGHTS_MCP_TOKEN)
README.md                                 (MODIFIED — document the new capability)
```

Upstream source lives at `C:\Users\steve\mcp-servers\flights-mcp` (already cloned locally, `uv
sync` already run there) — Task 1 copies from that path.

---

### Task 1: Vendor flights-mcp source

**Files:**
- Create: `flights-mcp/pyproject.toml`
- Create: `flights-mcp/LICENSE`
- Create: `flights-mcp/src/flights/**` (whole tree, copied)

- [ ] **Step 1: Copy the upstream files**

```bash
mkdir -p "D:/projects/claude/HermesPlusOpenbrain/flights-mcp"
cp "C:/Users/steve/mcp-servers/flights-mcp/pyproject.toml" "D:/projects/claude/HermesPlusOpenbrain/flights-mcp/"
cp "C:/Users/steve/mcp-servers/flights-mcp/LICENSE" "D:/projects/claude/HermesPlusOpenbrain/flights-mcp/"
cp -r "C:/Users/steve/mcp-servers/flights-mcp/src" "D:/projects/claude/HermesPlusOpenbrain/flights-mcp/src"
```

- [ ] **Step 2: Verify the copy is complete and untouched**

```bash
diff -rq "C:/Users/steve/mcp-servers/flights-mcp/src" "D:/projects/claude/HermesPlusOpenbrain/flights-mcp/src"
```

Expected: no output (directories identical).

- [ ] **Step 3: Commit**

```bash
git add flights-mcp/pyproject.toml flights-mcp/LICENSE flights-mcp/src
git commit -m "vendor: add flights-mcp upstream source (unmodified)"
```

---

### Task 2: Fix FastMCP host binding

**Why:** `openbrain-mcp/app/server.py` binds `FastMCP(..., host="0.0.0.0")` because the SDK's
default (`127.0.0.1`) triggers a DNS-rebinding host-header check that 421s any request whose
`Host` header isn't a localhost variant — exactly what happens once Hermes calls this container by
its Docker service name (`flights-mcp:8081`). The vendored `services/search.py` doesn't have this
fix yet.

**Files:**
- Modify: `flights-mcp/src/flights/services/search.py:21`

- [ ] **Step 1: Apply the fix**

Change line 21 from:

```python
mcp = FastMCP("find-flights-mcp")
```

to:

```python
# host="0.0.0.0" (not the FastMCP default "127.0.0.1") disables the MCP SDK's
# DNS-rebinding host-header check, which otherwise 421s any request whose Host
# header isn't a localhost variant -- i.e. every real request once deployed
# (Hermes calls flights-mcp:8081, both on the same Docker network).
mcp = FastMCP("find-flights-mcp", host="0.0.0.0")
```

- [ ] **Step 2: Verify the change**

```bash
grep -n "FastMCP(" "D:/projects/claude/HermesPlusOpenbrain/flights-mcp/src/flights/services/search.py"
```

Expected: `mcp = FastMCP("find-flights-mcp", host="0.0.0.0")`

- [ ] **Step 3: Commit**

```bash
git add flights-mcp/src/flights/services/search.py
git commit -m "fix: bind flights-mcp FastMCP to 0.0.0.0 to avoid host-header 421s"
```

---

### Task 3: Write the HTTP transport wrapper

**Files:**
- Create: `flights-mcp/src/flights/http_server.py`

- [ ] **Step 1: Write the file**

```python
"""Streamable-HTTP transport for flights-mcp, mirroring openbrain-mcp/app/server.py."""
import os

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .services.search import mcp  # noqa: F401 -- registers the @mcp.tool() functions

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
    app = mcp.streamable_http_app()           # Starlette app serving MCP at /mcp
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.add_middleware(BearerAuthMiddleware)
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=8081)
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
cd "D:/projects/claude/HermesPlusOpenbrain/flights-mcp" && DUFFEL_API_KEY_LIVE=duffel_test_placeholder python -c "import sys; sys.path.insert(0, 'src'); from flights.http_server import build_app; build_app(); print('OK')"
```

Expected: `OK` (this only checks the import graph + `FastMCP`/Starlette wiring; it does not start a
server or call Duffel). If Python/deps aren't available locally, skip to Task 5 where the same
check happens inside the Docker build.

- [ ] **Step 3: Commit**

```bash
git add flights-mcp/src/flights/http_server.py
git commit -m "feat: add streamable-HTTP transport wrapper for flights-mcp"
```

---

### Task 4: Dockerfile and README

**Files:**
- Create: `flights-mcp/Dockerfile`
- Create: `flights-mcp/README.md`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
# pyproject.toml declares packages = ["src/flights"], so `pip install .` needs
# the package dir to exist for the metadata/egg_info step -- stub it out just
# for this layer so dependency resolution is cacheable independently of
# source changes (same trick as openbrain-mcp/Dockerfile).
RUN mkdir -p src/flights && touch src/flights/__init__.py && \
    python -m pip install --no-cache-dir . && \
    rm -rf src
COPY src ./src
EXPOSE 8081
CMD ["python", "-m", "flights.http_server"]
```

- [ ] **Step 2: Write the README**

```markdown
# flights-mcp (vendored)

Streamable-HTTP deployment of [ravinahp/flights-mcp](https://github.com/ravinahp/flights-mcp)
(MIT license) for the Hermes-Agent WhatsApp bot. Read-only flight search via the Duffel API --
cannot book flights or make charges.

Vendored, not a git submodule, so the one required fix (`host="0.0.0.0"` in
`src/flights/services/search.py` -- see Task 2 of the deployment plan) ships with the rest of this
repo. `src/flights/server.py` (the original stdio entrypoint) is kept for local debugging with
`npx @modelcontextprotocol/inspector`; the Docker image runs `http_server.py` instead.

## Environment variables

- `DUFFEL_API_KEY_LIVE` -- Duffel API key (test or live; env var name is fixed by upstream
  regardless of tier).
- `FLIGHTS_MCP_TOKEN` -- bearer token this service requires on every request except `/health`.

## Internal-only

This service has no Traefik route and is not reachable from the public internet -- only from
containers on the same Docker network (`hermes_net`), i.e. Hermes-Agent.
```

- [ ] **Step 3: Commit**

```bash
git add flights-mcp/Dockerfile flights-mcp/README.md
git commit -m "build: add Dockerfile and README for flights-mcp"
```

---

### Task 5: Local build and health/auth verification

**Files:** none (verification only)

- [ ] **Step 1: Build the image**

```bash
cd "D:/projects/claude/HermesPlusOpenbrain" && docker build -t flights-mcp-local ./flights-mcp
```

Expected: build succeeds, ends with `naming to docker.io/library/flights-mcp-local`.

- [ ] **Step 2: Run it locally with a placeholder key**

```bash
docker run --rm -d --name flights-mcp-local -p 8081:8081 \
  -e DUFFEL_API_KEY_LIVE=duffel_test_placeholder \
  -e FLIGHTS_MCP_TOKEN=local-test-token \
  flights-mcp-local
```

- [ ] **Step 3: Verify health check without auth (should still work, /health is exempt)**

```bash
curl -s http://localhost:8081/health
```

Expected: `{"ok":true}`

- [ ] **Step 4: Verify auth is enforced on the MCP endpoint**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8081/mcp
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer wrong-token" http://localhost:8081/mcp
```

Expected: both `401`.

- [ ] **Step 5: Stop the local container**

```bash
docker stop flights-mcp-local
```

No commit (verification only).

---

### Task 6: Add the service to docker-compose and .env.example

**Files:**
- Modify: `deploy/docker-compose.openbrain.yml`
- Modify: `deploy/.env.example`

- [ ] **Step 1: Add the `flights-mcp` service block**

Insert after the `openbrain-mcp` service block (before `openbrain-gui`) in
`deploy/docker-compose.openbrain.yml`:

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

- [ ] **Step 2: Add the new env vars to `.env.example`**

Append to `deploy/.env.example`:

```
# flights-mcp
DUFFEL_API_KEY_LIVE=duffel_test_your_key_here
FLIGHTS_MCP_TOKEN=change-me-long-random
```

- [ ] **Step 3: Validate compose syntax**

```bash
cd "D:/projects/claude/HermesPlusOpenbrain/deploy" && docker compose -f docker-compose.openbrain.yml config --quiet
```

Expected: no output, exit code 0. (Will fail here on missing `${OPENBRAIN_HOST}` etc. unless a
local `.env` exists -- that's expected off the VPS; the point of this step is catching YAML syntax
errors, not full validation. If it errors specifically on undefined variables, that's fine to
ignore locally.)

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.openbrain.yml deploy/.env.example
git commit -m "feat: add flights-mcp service to docker-compose, internal-only"
```

---

### Task 7: `[VPS]` Deploy and verify health

**These steps run on the Hostinger VPS, in the user's own SSH session — not executed by Claude.**

- [ ] **Step 1: Pull latest and set secrets**

```bash
cd ~/HermesPlusOpenbrain && git pull origin main
```

Edit `deploy/.env` on the VPS (not committed) and add:
```
DUFFEL_API_KEY_LIVE=<your actual duffel_test key>
FLIGHTS_MCP_TOKEN=<output of: openssl rand -hex 32>
```

- [ ] **Step 2: Build and start**

```bash
cd deploy && docker compose -f docker-compose.openbrain.yml up -d --build flights-mcp
```

- [ ] **Step 3: Verify it's healthy**

```bash
docker compose -f docker-compose.openbrain.yml ps flights-mcp
```

Expected: `STATUS` column shows `Up ... (healthy)`.

- [ ] **Step 4: Verify Hermes-network reachability from inside the Hermes container**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 curl -s http://flights-mcp:8081/health
```

(Adjust the container name to match `docker ps` output if different.) Expected: `{"ok":true}`.

No commit (VPS operational step).

---

### Task 8: `[VPS]` Register with Hermes

**These steps run on the Hostinger VPS, in the user's own SSH session.**

- [ ] **Step 1: Register the MCP server**

```bash
hermes mcp configure
```

Follow the interactive prompts: name `flights-mcp`, URL `http://flights-mcp:8081/mcp`, header
`Authorization: Bearer <FLIGHTS_MCP_TOKEN value from deploy/.env>`.

- [ ] **Step 2: Verify registration**

```bash
hermes mcp catalog
```

Expected: `flights-mcp` listed with its three tools (`search_flights`, `get_offer_details`,
`search_multi_city`) and a reachable/healthy status.

No commit (VPS operational step).

---

### Task 9: `[VPS]` End-to-end WhatsApp test

**Run by the user, not Claude.**

- [ ] **Step 1: Ask Hermes a flight-search question over WhatsApp**

Example: "Find me a one-way flight from VIE to LHR next Tuesday."

- [ ] **Step 2: Confirm the reply**

Expected: Hermes replies with one or more flight offers (simulated/test-key data — prices and
times won't be real, but the shape should be sane: airline, times, price, stops).

No commit (manual acceptance test).

---

### Task 10: Document the new capability

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a short section describing flights-mcp**

Add a paragraph to `README.md` (near the existing MCP-servers/capabilities description) noting:
Hermes can now search flights via `flights-mcp` (Duffel API, read-only, currently on a test key so
results are simulated), deployed internal-only on the Hermes Docker network — no public endpoint,
unlike `openbrain-mcp`.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document flights-mcp deployment"
```

---

## Self-Review Notes

- **Spec coverage:** §5.1 (vendoring + `host="0.0.0.0"` fix + `http_server.py`) → Tasks 1-3. §5.1
  Dockerfile/README → Task 4. §5.2/§5.3 (compose block + env vars) → Task 6. §5.4 (Hermes
  registration) → Task 8. §8 testing steps 1-6 → Tasks 5, 7, 8, 9. §10 (README update) → Task 10.
  No spec section without a corresponding task.
- **Placeholder scan:** no TBD/TODO; all code blocks are complete, copy-pasteable content pulled
  directly from the approved spec.
- **Type/name consistency:** `FLIGHTS_MCP_TOKEN` used identically in Task 3's wrapper, Task 6's
  compose block, Task 7's VPS `.env`, and Task 8's registration header. Port `8081` consistent
  across Tasks 3, 4, 5, 6, 7. Service/container name `flights-mcp` consistent throughout.
