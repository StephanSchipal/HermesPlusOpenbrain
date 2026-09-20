# OpenBrain MCP OAuth Shim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let claude.ai's personal "Add custom connector" (web + mobile) connect to `openbrain-mcp` via automatic OAuth Dynamic Client Registration, with no manual credential entry — while every existing bearer-token client (Claude Code, Claude Desktop, `openbrain-gui`, Hermes) keeps working completely unchanged.

**Architecture:** A new `app/oauth.py` module inside `openbrain-mcp` implements a minimal RFC 7591 (DCR) + RFC 8414 + RFC 9728 + PKCE (S256) OAuth 2.0 authorization server, wired into the existing Starlette app in `server.py`. The `/token` endpoint hands back the existing `OPENBRAIN_TOKEN` verbatim as the access token — the OAuth layer only decides *whether* to release the token, it never mints a separate one, so `BearerAuthMiddleware`'s actual check is untouched. The one browser-facing endpoint, `/authorize`, is gated by a new Traefik router reusing the GUI's existing basic-auth. All OAuth state (registered clients, in-flight authorization codes) is in-memory, deliberately non-durable across restarts.

**Tech Stack:** Python 3.11, Starlette (routes/middleware), `hashlib`/`base64`/`secrets` (stdlib, for PKCE), pytest + `starlette.testclient.TestClient` (existing test stack — no new dependencies).

**Design spec:** [`docs/superpowers/specs/2026-09-20-openbrain-mcp-oauth-connector-design.md`](../specs/2026-09-20-openbrain-mcp-oauth-connector-design.md) — read it first for the full rationale behind each decision below.

---

## File Structure

- Modify: `openbrain-mcp/app/config.py` — add `OPENBRAIN_HOST`.
- Create: `openbrain-mcp/app/oauth.py` — the OAuth authorization server (metadata endpoints, DCR, authorize, token).
- Create: `openbrain-mcp/tests/test_oauth.py` — unit tests for `oauth.py`, using a minimal standalone Starlette test app (not the full `openbrain-mcp` app — that integration is tested separately in `test_server.py`).
- Modify: `openbrain-mcp/app/server.py` — wire the 5 new routes into `build_app()`; update `BearerAuthMiddleware` to exempt them and add the `WWW-Authenticate` header on 401.
- Modify: `openbrain-mcp/tests/test_server.py` — add tests for the middleware changes.
- Modify: `deploy/docker-compose.openbrain.yml` — pass `OPENBRAIN_HOST` into the container; add the `/authorize` Traefik router.
- Modify: `README.md` — document the new connector path.

---

### Task 1: Add `OPENBRAIN_HOST` to config

**Files:**
- Modify: `openbrain-mcp/app/config.py`

- [x] **Step 1: Add the new setting**

In `openbrain-mcp/app/config.py`, add this line after `OPENBRAIN_TOKEN` (no test needed — this file has no existing tests; it's a plain `os.environ.get` read, exactly like every other line in it):

```python
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
OPENBRAIN_HOST = os.environ.get("OPENBRAIN_HOST", "")
```

- [x] **Step 2: Commit**

```bash
cd openbrain-mcp
git add app/config.py
git commit -m "feat(oauth): add OPENBRAIN_HOST config setting"
```

**Done:** `fb7e7d2`

---

### Task 2: OAuth metadata endpoints (`.well-known`)

**Files:**
- Create: `openbrain-mcp/app/oauth.py`
- Create: `openbrain-mcp/tests/test_oauth.py`

- [x] **Step 1: Write the failing tests**

Create `openbrain-mcp/tests/test_oauth.py`:

```python
# tests/test_oauth.py
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import app.oauth as oauth_module


def _oauth_test_app() -> Starlette:
    """A standalone app exposing only the oauth.py routes, for testing the
    module in isolation from the full openbrain-mcp app (that integration is
    covered separately in test_server.py)."""
    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server",
              oauth_module.well_known_auth_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              oauth_module.well_known_protected_resource, methods=["GET"]),
    ])


def _client(monkeypatch, host: str = "brain.test.example") -> TestClient:
    monkeypatch.setattr(oauth_module, "OPENBRAIN_HOST", host)
    return TestClient(_oauth_test_app())


def test_well_known_authorization_server_metadata_shape(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issuer"] == "https://brain.test.example"
    assert body["authorization_endpoint"] == "https://brain.test.example/authorize"
    assert body["token_endpoint"] == "https://brain.test.example/token"
    assert body["registration_endpoint"] == "https://brain.test.example/register"
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == ["authorization_code"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


def test_well_known_protected_resource_metadata_shape(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource"] == "https://brain.test.example/mcp"
    assert body["authorization_servers"] == ["https://brain.test.example"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `ModuleNotFoundError: No module named 'app.oauth'` (or collection error) — the module doesn't exist yet.

- [x] **Step 3: Create `app/oauth.py` with the two metadata handlers**

```python
# app/oauth.py
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import OPENBRAIN_HOST


async def well_known_auth_server(_request: Request) -> JSONResponse:
    return JSONResponse({
        "issuer": f"https://{OPENBRAIN_HOST}",
        "authorization_endpoint": f"https://{OPENBRAIN_HOST}/authorize",
        "token_endpoint": f"https://{OPENBRAIN_HOST}/token",
        "registration_endpoint": f"https://{OPENBRAIN_HOST}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


async def well_known_protected_resource(_request: Request) -> JSONResponse:
    return JSONResponse({
        "resource": f"https://{OPENBRAIN_HOST}/mcp",
        "authorization_servers": [f"https://{OPENBRAIN_HOST}"],
    })
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `2 passed`

- [x] **Step 5: Commit**

```bash
cd openbrain-mcp
git add app/oauth.py tests/test_oauth.py
git commit -m "feat(oauth): add OAuth metadata well-known endpoints"
```

**Done:** `3814a45`

---

### Task 3: Dynamic Client Registration (`/register`)

**Files:**
- Modify: `openbrain-mcp/app/oauth.py`
- Modify: `openbrain-mcp/tests/test_oauth.py`

- [x] **Step 1: Write the failing tests**

In `tests/test_oauth.py`, update `_oauth_test_app()` to add the `/register` route, and add an autouse fixture that clears the module's in-memory client store between tests (it's a module-level dict, so state would otherwise leak across tests in the same process):

```python
import pytest

# add near the top, after the existing imports
@pytest.fixture(autouse=True)
def _clear_oauth_state():
    oauth_module._clients.clear()
    yield
    oauth_module._clients.clear()


def _oauth_test_app() -> Starlette:
    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server",
              oauth_module.well_known_auth_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              oauth_module.well_known_protected_resource, methods=["GET"]),
        Route("/register", oauth_module.register, methods=["POST"]),
    ])
```

Add these two tests at the end of the file:

```python
def test_register_returns_client_id_for_valid_metadata(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert "client_id" in body and body["client_id"]
    assert body["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
    assert body["token_endpoint_auth_method"] == "none"
    # the registered client_id must actually be usable later
    assert body["client_id"] in oauth_module._clients


def test_register_rejects_missing_redirect_uris(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `FAILED ... AttributeError: module 'app.oauth' has no attribute 'register'` (and no `_clients` attribute either)

- [x] **Step 3: Add the client store and `register` handler to `app/oauth.py`**

```python
# add these imports at the top of app/oauth.py
import secrets
import time

# add after the existing imports, before the two well-known handlers
_clients: dict[str, dict] = {}


# add after well_known_protected_resource
async def register(request: Request) -> JSONResponse:
    body = await request.json()
    redirect_uris = body.get("redirect_uris") or []
    if not redirect_uris:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    client_id = secrets.token_urlsafe(24)
    _clients[client_id] = {"redirect_uris": redirect_uris, "created_at": time.time()}
    return JSONResponse({
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    })
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `4 passed`

- [x] **Step 5: Commit**

```bash
cd openbrain-mcp
git add app/oauth.py tests/test_oauth.py
git commit -m "feat(oauth): add RFC 7591 dynamic client registration endpoint"
```

**Done:** `2601246`, plus three review-driven follow-up commits: `3e60b48` (reject malformed body / non-list `redirect_uris`), `50525b5` (test coverage for the non-object-JSON-body guard), and `a504167` (pin `redirect_uris` to an allowlist of the real claude.ai callback URL — closes an open-redirect/token-theft gap found during Task 4's review; spec amended in `5df53b6`).

---

### Task 4: Authorization endpoint with PKCE (`/authorize`)

**Files:**
- Modify: `openbrain-mcp/app/oauth.py`
- Modify: `openbrain-mcp/tests/test_oauth.py`

- [x] **Step 1: Write the failing tests**

In `tests/test_oauth.py`:

1. Update the autouse fixture to also clear `_auth_codes`:

```python
@pytest.fixture(autouse=True)
def _clear_oauth_state():
    oauth_module._clients.clear()
    oauth_module._auth_codes.clear()
    yield
    oauth_module._clients.clear()
    oauth_module._auth_codes.clear()
```

2. Add the `/authorize` route to `_oauth_test_app()`:

```python
def _oauth_test_app() -> Starlette:
    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server",
              oauth_module.well_known_auth_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              oauth_module.well_known_protected_resource, methods=["GET"]),
        Route("/register", oauth_module.register, methods=["POST"]),
        Route("/authorize", oauth_module.authorize, methods=["GET"]),
    ])
```

3. Add a small PKCE helper and a `_register_client` helper for test setup, plus the tests, at the end of the file:

```python
import base64
import hashlib


def _pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) for a valid S256 PKCE pair."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _register_client(client: TestClient, redirect_uri: str) -> str:
    resp = client.post("/register", json={"redirect_uris": [redirect_uri]})
    return resp.json()["client_id"]


REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def test_authorize_issues_code_and_redirects(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    _verifier, challenge = _pkce_pair()

    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
    }, follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(f"{REDIRECT_URI}?")
    assert "code=" in location
    assert "state=xyz" in location


def test_authorize_rejects_unknown_client_id(monkeypatch):
    client = _client(monkeypatch)
    _verifier, challenge = _pkce_pair()
    resp = client.get("/authorize", params={
        "client_id": "does-not-exist",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_authorize_rejects_unregistered_redirect_uri(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    _verifier, challenge = _pkce_pair()
    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": "https://attacker.example/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client"


def test_authorize_rejects_missing_pkce(monkeypatch):
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `FAILED ... AttributeError: module 'app.oauth' has no attribute 'authorize'` (and no `_auth_codes` attribute)

- [x] **Step 3: Add `_auth_codes`, the PKCE verifier, and the `authorize` handler to `app/oauth.py`**

```python
# add these imports at the top of app/oauth.py
from urllib.parse import urlencode

from starlette.responses import JSONResponse, RedirectResponse   # extends the existing import line

# add near _clients
CODE_TTL_SECONDS = 60
_auth_codes: dict[str, dict] = {}


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, code_challenge)


# add after register(); this module now also needs `import hashlib` and
# `import base64` at the top, alongside the existing `secrets`/`time` imports
async def authorize(request: Request):
    q = request.query_params
    client = _clients.get(q.get("client_id", ""))
    redirect_uri = q.get("redirect_uri", "")
    if not client or redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        return JSONResponse(
            {"error": "invalid_request", "error_description": "PKCE S256 required"},
            status_code=400,
        )
    code = secrets.token_urlsafe(24)
    _auth_codes[code] = {
        "client_id": q["client_id"],
        "code_challenge": q["code_challenge"],
        "redirect_uri": redirect_uri,
        "expires_at": time.time() + CODE_TTL_SECONDS,
    }
    params = {"code": code}
    if q.get("state") is not None:
        params["state"] = q["state"]
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)
```

Full top-of-file import block for `app/oauth.py` after this step (shown for clarity — write the file with exactly these imports):

```python
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.config import OPENBRAIN_HOST
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `8 passed`

- [x] **Step 5: Commit**

```bash
cd openbrain-mcp
git add app/oauth.py tests/test_oauth.py
git commit -m "feat(oauth): add PKCE-protected /authorize endpoint"
```

**Done:** `cbb4226`. Code review of this task surfaced a design-level open-redirect/token-theft gap in `/register` (Task 3) — addressed via spec amendment `5df53b6` and fix `a504167` (see Task 3's Done line).

---

### Task 5: Token endpoint (`/token`)

**Files:**
- Modify: `openbrain-mcp/app/oauth.py`
- Modify: `openbrain-mcp/tests/test_oauth.py`

- [x] **Step 1: Write the failing tests**

In `tests/test_oauth.py`, add the `/token` route to `_oauth_test_app()`:

```python
def _oauth_test_app() -> Starlette:
    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server",
              oauth_module.well_known_auth_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              oauth_module.well_known_protected_resource, methods=["GET"]),
        Route("/register", oauth_module.register, methods=["POST"]),
        Route("/authorize", oauth_module.authorize, methods=["GET"]),
        Route("/token", oauth_module.token, methods=["POST"]),
    ])
```

Add a helper that drives the full flow up to getting a code, plus the token tests, at the end of the file:

```python
from urllib.parse import parse_qs, urlsplit


def _get_auth_code(client: TestClient, client_id: str, code_verifier_challenge: tuple[str, str]) -> str:
    _verifier, challenge = code_verifier_challenge
    resp = client.get("/authorize", params={
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, follow_redirects=False)
    location = resp.headers["location"]
    query = parse_qs(urlsplit(location).query)
    return query["code"][0]


def test_full_authorize_token_flow_with_valid_pkce(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "the-real-secret-token"
    assert body["token_type"] == "Bearer"
    assert "expires_in" not in body
    assert "refresh_token" not in body


def test_token_rejects_expired_code(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))
    oauth_module._auth_codes[code]["expires_at"] = time.time() - 1  # force expiry

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_replayed_code(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))
    token_request = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": verifier,
    }

    first = client.post("/token", data=token_request)
    second = client.post("/token", data=token_request)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_token_rejects_wrong_code_verifier(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": "wrong-verifier",
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_rejects_client_id_mismatch(monkeypatch):
    monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", "the-real-secret-token")
    client = _client(monkeypatch)
    client_id = _register_client(client, REDIRECT_URI)
    other_client_id = _register_client(client, REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    code = _get_auth_code(client, client_id, (verifier, challenge))

    resp = client.post("/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": other_client_id,
        "code_verifier": verifier,
    })

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"
```

Also add `import time` at the top of `tests/test_oauth.py` (used by the expired-code test).

- [x] **Step 2: Run tests to verify they fail**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `FAILED ... AttributeError: module 'app.oauth' has no attribute 'token'`

- [x] **Step 3: Add the `token` handler to `app/oauth.py`**

```python
# add after authorize()
async def token(request: Request) -> JSONResponse:
    form = await request.form()
    code = form.get("code", "")
    entry = _auth_codes.pop(code, None)   # pop, not get: makes the code single-use
    if not entry or entry["expires_at"] < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["client_id"] != form.get("client_id"):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    verifier = form.get("code_verifier", "")
    if not _verify_pkce(verifier, entry["code_challenge"]):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse({
        "access_token": OPENBRAIN_TOKEN,
        "token_type": "Bearer",
    })
```

This needs `OPENBRAIN_TOKEN` importable from `app.oauth` (so tests can `monkeypatch.setattr(oauth_module, "OPENBRAIN_TOKEN", ...)`, matching the exact pattern `test_server.py` already uses for `server_module.OPENBRAIN_TOKEN`). Update the `app.config` import line at the top of `app/oauth.py`:

```python
from app.config import OPENBRAIN_HOST, OPENBRAIN_TOKEN
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd openbrain-mcp && python -m pytest tests/test_oauth.py -v`
Expected: `13 passed`

- [x] **Step 5: Commit**

```bash
cd openbrain-mcp
git add app/oauth.py tests/test_oauth.py
git commit -m "feat(oauth): add /token endpoint, completing the DCR+PKCE flow"
```

**Done:** `6a8e963`, plus two review-driven follow-up commits: `cf3ce8c` (reject malformed/type-confused `/token` requests before consuming the code; add `grant_type`/`redirect_uri` checks) and `21f82dd` (defense-in-depth `redirect_uri` type check). The full register→authorize→token flow is now complete and tested end-to-end.

---

### Task 6: Wire OAuth routes into the real app and update the auth middleware

**Files:**
- Modify: `openbrain-mcp/app/server.py`
- Modify: `openbrain-mcp/tests/test_server.py`

- [x] **Step 1: Write the failing tests**

Add to `tests/test_server.py` (after the existing tests):

```python
def test_well_known_endpoints_exempt_from_bearer_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200


def test_register_exempt_from_bearer_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})
    assert resp.status_code == 200


def test_mcp_401_includes_www_authenticate_header(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(server_module, "OPENBRAIN_HOST", "brain.test.example")
    resp = client.get("/mcp")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == (
        'Bearer resource_metadata='
        '"https://brain.test.example/.well-known/oauth-protected-resource"'
    )
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd openbrain-mcp && python -m pytest tests/test_server.py -v`
Expected: the three new tests fail — the first two with 401 (routes not registered/still behind bearer auth), the third because `server_module.OPENBRAIN_HOST` doesn't exist and the header is missing.

- [x] **Step 3: Update `app/server.py`**

Change the import line near the top:

```python
from app.config import OPENBRAIN_TOKEN
```
to:
```python
from app.config import OPENBRAIN_HOST, OPENBRAIN_TOKEN
```

Add an import for the oauth handlers, right after the `app.fingerprint` import:

```python
from app.fingerprint import content_fingerprint_debug
from app.oauth import (
    authorize,
    register,
    token,
    well_known_auth_server,
    well_known_protected_resource,
)
```

Replace the `BearerAuthMiddleware` class:

```python
class BearerAuthMiddleware(BaseHTTPMiddleware):
    # /authorize needs no bearer token -- it's gated one layer further out,
    # by Traefik basic-auth on that path (see deploy/docker-compose.openbrain.yml).
    # The rest are pre-auth OAuth endpoints a client hasn't obtained a token
    # from yet, plus the pre-existing /health exemption.
    EXEMPT_PATHS = {
        "/health",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/register",
        "/authorize",
        "/token",
    }

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        expected = f"Bearer {OPENBRAIN_TOKEN}"
        if not OPENBRAIN_TOKEN or request.headers.get("authorization") != expected:
            resource_meta = f"https://{OPENBRAIN_HOST}/.well-known/oauth-protected-resource"
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource_meta}"'},
            )
        return await call_next(request)
```

Update `build_app()` to register the five new routes:

```python
def build_app() -> Starlette:
    app = mcp.streamable_http_app()           # Starlette app serving MCP at /mcp
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.router.routes.append(Route(
        "/.well-known/oauth-authorization-server", well_known_auth_server, methods=["GET"]))
    app.router.routes.append(Route(
        "/.well-known/oauth-protected-resource", well_known_protected_resource, methods=["GET"]))
    app.router.routes.append(Route("/register", register, methods=["POST"]))
    app.router.routes.append(Route("/authorize", authorize, methods=["GET"]))
    app.router.routes.append(Route("/token", token, methods=["POST"]))
    app.add_middleware(BearerAuthMiddleware)
    return app
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd openbrain-mcp && python -m pytest tests/ -v`
Expected: all tests pass, including the full pre-existing suite (no regressions) plus the 13 `test_oauth.py` tests and the 3 new `test_server.py` tests.

- [x] **Step 5: Commit**

```bash
cd openbrain-mcp
git add app/server.py tests/test_server.py
git commit -m "feat(oauth): wire OAuth routes into the app, update bearer middleware"
```

**Done:** `99adcae`, plus review-driven follow-up `171c418` (corrected an inaccurate `/authorize` exemption comment — it claimed Traefik basic-auth already gates that path, which is only true after Task 7 — and added the two missing `/authorize`/`/token` exemption regression tests). Confirmed `/mcp` is not in `EXEMPT_PATHS` — every existing client (Hermes, Claude Code, Claude Desktop, GUI) is unaffected.

---

### Task 7: Deploy config — Traefik router for `/authorize`

**Files:**
- Modify: `deploy/docker-compose.openbrain.yml`

- [x] **Step 1: Add `OPENBRAIN_HOST` to the `openbrain-mcp` service's environment**

In `deploy/docker-compose.openbrain.yml`, find the `openbrain-mcp` service's `environment:` block:

```yaml
  openbrain-mcp:
    build: ../openbrain-mcp
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://openbrain:${POSTGRES_PASSWORD}@openbrain-db:5432/openbrain
      OPENBRAIN_TOKEN: ${OPENBRAIN_TOKEN}
      OPENBRAIN_TIMEZONE: ${OPENBRAIN_TIMEZONE:-UTC}
```

Add the new line:

```yaml
  openbrain-mcp:
    build: ../openbrain-mcp
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://openbrain:${POSTGRES_PASSWORD}@openbrain-db:5432/openbrain
      OPENBRAIN_TOKEN: ${OPENBRAIN_TOKEN}
      OPENBRAIN_TIMEZONE: ${OPENBRAIN_TIMEZONE:-UTC}
      OPENBRAIN_HOST: ${OPENBRAIN_HOST}
```

- [x] **Step 2: Add the `/authorize` Traefik router to the same service's `labels:`**

Find:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.openbrain.rule=Host(`${OPENBRAIN_HOST}`)"
      - "traefik.http.routers.openbrain.entrypoints=websecure"
      - "traefik.http.routers.openbrain.tls.certresolver=letsencrypt"
      - "traefik.http.services.openbrain.loadbalancer.server.port=8080"
      # No Traefik network membership needed: Traefik runs with network_mode: host
      # and already reaches container bridge IPs directly (confirmed in Task 0.3).
```

Replace with:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.openbrain.rule=Host(`${OPENBRAIN_HOST}`)"
      - "traefik.http.routers.openbrain.entrypoints=websecure"
      - "traefik.http.routers.openbrain.tls.certresolver=letsencrypt"
      - "traefik.http.services.openbrain.loadbalancer.server.port=8080"
      - "traefik.http.routers.openbrain.priority=1"
      # No Traefik network membership needed: Traefik runs with network_mode: host
      # and already reaches container bridge IPs directly (confirmed in Task 0.3).
      # /authorize is the one browser-facing OAuth step (see openbrain-mcp-oauth-
      # connector-design.md) -- gated by the same basic-auth already protecting
      # the GUI, reusing its middleware and this service (no separate service
      # definition needed). Everything else on this host (/mcp, /token,
      # /register, /.well-known/*) stays reachable without it: those are
      # machine-to-machine calls from Claude's backend, never a browser.
      #
      # NOTE (found during Task 7 implementation, corrected in the spec and
      # here): both routers need an EXPLICIT priority. Traefik compares
      # priorities numerically whether they're explicit or automatically
      # computed from rule length -- an earlier draft left `openbrain`
      # unset and gave only `openbrain-authorize` a small explicit value
      # (10), which is LOWER than `openbrain`'s automatic rule-length
      # priority (35-45 for a real hostname), so the unprotected router
      # would have silently won every /authorize request. 100 vs. 1 removes
      # any dependency on hostname length or automatic/explicit tie-breaking.
      - "traefik.http.routers.openbrain-authorize.rule=Host(`${OPENBRAIN_HOST}`) && Path(`/authorize`)"
      - "traefik.http.routers.openbrain-authorize.entrypoints=websecure"
      - "traefik.http.routers.openbrain-authorize.tls.certresolver=letsencrypt"
      - "traefik.http.routers.openbrain-authorize.priority=100"
      - "traefik.http.routers.openbrain-authorize.middlewares=openbrain-gui-auth"
      - "traefik.http.routers.openbrain-authorize.service=openbrain"
```

- [x] **Step 3: Validate the compose file parses correctly**

This step needs `.env` present (even with placeholder values) since compose interpolates `${OPENBRAIN_HOST}` etc. at parse time.

Run:
```bash
cd deploy
test -f .env || cp .env.example .env
docker compose -f docker-compose.openbrain.yml config --quiet
```
Expected: no output, exit code 0 (means the YAML + label interpolation is syntactically valid). If you created a throwaway `.env` just for this check and don't already have a real one on this machine, that's fine — this step only validates syntax, it doesn't deploy anything.

- [x] **Step 4: Commit**

```bash
git add deploy/docker-compose.openbrain.yml
git commit -m "feat(oauth): add Traefik router gating /authorize behind the GUI's basic-auth"
```

**Done:** `9d355c2`, plus review-driven follow-up `7c665ab` (fixed a real bug found during implementer self-review: the original `priority=10` on `openbrain-authorize` was actually LOWER than `openbrain`'s automatic rule-length priority, so the unprotected plain router would have silently won every `/authorize` request — verified against Traefik's own docs. Both routers now set explicit priorities, `100`/`1`). Design spec and this plan's Task 7 code block corrected to match.

---

### Task 8: Document the new connector path in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a new subsection after the existing Claude Desktop paragraph**

In `README.md`, find this paragraph (end of "## Phase 6 — Laptop clients"):

```
`[System.IO.File]::WriteAllText(...)` instead. Full writeup in the plan's Task 6.1 resolution note.

## Using it
```

Insert a new subsection between them:

```
`[System.IO.File]::WriteAllText(...)` instead. Full writeup in the plan's Task 6.1 resolution note.

**claude.ai personal connector (web + mobile), done (2026-09-20).** Unlike Claude Code/Desktop,
claude.ai's personal "Add custom connector" dialog has no field for a raw bearer token — only a
Server URL and optional OAuth Client ID/Secret. `openbrain-mcp` now also runs a minimal OAuth 2.0
authorization server (RFC 7591 Dynamic Client Registration + PKCE) alongside its existing bearer-
token check, so claude.ai can self-register and authenticate automatically: paste
`https://<OPENBRAIN_HOST>/mcp` into the connector dialog, leave Client ID/Secret blank. The one
browser-facing step (`/authorize`) is gated by the same Traefik basic-auth already protecting
`openbrain-gui`. The OAuth flow's `/token` endpoint hands back the existing `OPENBRAIN_TOKEN`
itself as the access token — it's a login wrapper around the same secret every other client uses,
not a parallel credential system. Design/implementation details:
[`docs/superpowers/specs/2026-09-20-openbrain-mcp-oauth-connector-design.md`](docs/superpowers/specs/2026-09-20-openbrain-mcp-oauth-connector-design.md).

## Using it
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document the claude.ai personal connector OAuth path"
```

---

### Task 9: Deploy and manual smoke test

**Files:** none (VPS operations only)

- [ ] **Step 1: Deploy to the VPS**

Follow the established update procedure (`ssh root@srv1608402.hstgr.cloud`, `git pull --ff-only origin main` in `/root/HermesPlusOpenbrain`, then rebuild just the changed service):

```bash
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain && git pull --ff-only origin main"
ssh root@srv1608402.hstgr.cloud "cd /root/HermesPlusOpenbrain/deploy && docker compose -f docker-compose.openbrain.yml up -d --build openbrain-mcp"
```

- [ ] **Step 2: Verify the container is healthy and the new endpoints respond**

```bash
ssh root@srv1608402.hstgr.cloud "docker compose -f /root/HermesPlusOpenbrain/deploy/docker-compose.openbrain.yml ps"
curl -s https://brain.srv1608402.hstgr.cloud/.well-known/oauth-authorization-server
curl -s https://brain.srv1608402.hstgr.cloud/.well-known/oauth-protected-resource
curl -s -o /dev/null -w "%{http_code}\n" https://brain.srv1608402.hstgr.cloud/mcp   # expect 401
curl -s -I https://brain.srv1608402.hstgr.cloud/mcp | grep -i www-authenticate      # expect the header
```
Expected: both `.well-known` calls return JSON with `https://brain.srv1608402.hstgr.cloud` URLs; `/mcp` still returns `401`; the `WWW-Authenticate` header is present.

- [ ] **Step 3: Verify `/authorize` requires basic-auth and existing clients still work**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://brain.srv1608402.hstgr.cloud/authorize?client_id=x&redirect_uri=y&code_challenge=z&code_challenge_method=S256"
```
Expected: `401` from Traefik's basic-auth (not from the app) — confirms the router+middleware wiring, before ever reaching the `oauth.py` handler.

Then confirm nothing existing broke:
- From Claude Code or Claude Desktop (already configured with the static token): run a real tool call, e.g. ask it to search OpenBrain for something known to exist.
- Open `gui.srv1608402.hstgr.cloud` in a browser and confirm it still loads and lists captures.

- [ ] **Step 4: Add the connector in claude.ai and confirm end-to-end**

On the phone (or web), Settings → Connectors → Add custom connector:
- URL: `https://brain.srv1608402.hstgr.cloud/mcp`
- Leave Client ID / Client Secret blank.
- Confirm a basic-auth prompt appears (GUI credentials) before the connector shows as connected.
- Ask Claude something that requires a real tool call against it (e.g. "how many notes do I have saved in OpenBrain?" or "search OpenBrain for X" for something known to be there) and confirm it returns real data, not an error.

- [ ] **Step 5: Update project memory**

This is a deploy-verification task, not a code change — no commit here. Record in the project's memory system (per this repo's existing convention of tracking VPS-side feature rollouts) that the OAuth shim is live, the date, and the verified connector URL, so a future session doesn't need to re-derive this from git history.
