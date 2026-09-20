# OpenBrain MCP — OAuth Shim for the claude.ai Personal Connector — Design Spec

**Date:** 2026-09-20
**Author:** Stephan (with Claude, brainstorming session 2026-09-20)
**Status:** Approved design — ready for implementation planning

---

## 1. Goal

Let claude.ai's personal "Add custom connector" flow (web **and mobile**)
connect to `openbrain-mcp` with no manual credential entry, by adding a
minimal OAuth 2.0 authorization server in front of the existing bearer-token
check.

Today, `openbrain-mcp` authenticates every request (except `/health`) with a
single static bearer token (`OPENBRAIN_TOKEN`), checked by
`BearerAuthMiddleware` in `server.py`. Claude Code, Claude Desktop, and the
`openbrain-gui` backend all already work this way, with the token configured
directly in each client. claude.ai's personal connector UI has no field for a
raw bearer token, though — only a Server URL and optional OAuth Client
ID/Secret — so this server, as built, cannot be added there at all.

## 2. Context & constraints

- Confirmed via Anthropic's current connector docs
  (`https://claude.com/docs/connectors/building/authentication`): Claude
  supports `oauth_dcr` (Dynamic Client Registration, RFC 7591) "out of the
  box" — no manual Client ID/Secret needed if the server's authorization
  server exposes a `registration_endpoint`. This is the path this spec
  builds.
- Claude's OAuth discovery flow (per the same doc): on a `401` from the
  resource server, it reads the `WWW-Authenticate: Bearer
  resource_metadata="..."` header, fetches that protected-resource metadata
  (RFC 9728), follows `authorization_servers` to the issuer, fetches its
  metadata (RFC 8414), and if a `registration_endpoint` is present, POSTs
  there to self-register before starting the authorization-code + PKCE flow.
- `openbrain-mcp` is a single-user, single-secret service. The only thing
  worth protecting the OAuth layer against is a stranger who discovers the
  hostname completing the flow *without* knowing `OPENBRAIN_TOKEN` — there is
  no multi-tenant concern, no need for per-client scoping, and no existing
  clients to migrate off the static token.
- `openbrain-gui` already has a working single-user gate for a browser-facing
  endpoint: Traefik `basicauth` middleware on its router, credentials from
  `GUI_BASIC_AUTH_USERS` (`deploy/docker-compose.openbrain.yml`). This spec
  reuses that exact mechanism and credential for the one new
  browser-reachable endpoint (`/authorize`), rather than inventing a new
  login/session system.
- Decisions locked in during brainstorming (see table below) prioritize
  minimum new infrastructure: everything lives inside `openbrain-mcp`'s
  existing Starlette app, state is in-memory, and the OAuth access token
  handed back *is* the existing `OPENBRAIN_TOKEN` — the OAuth layer is purely
  a login/consent wrapper around the secret that already exists, not a
  parallel credential system.

## 3. Key design decisions (made during brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| Where the shim lives | **Inside `openbrain-mcp`'s existing Starlette app** (new `app/oauth.py` module, wired into `build_app()`) | No new container, compose service, network hop, or Dockerfile. Keeps the one security boundary in the one file that already owns it. Rejected: a separate sidecar proxy container (real extra infra for a login wrapper around one secret); a third-party OAuth-for-MCP proxy (new dependency to trust for a narrow, well-specified need). |
| Client registration | **Dynamic Client Registration (RFC 7591)**, not CIMD or manual Client ID/Secret entry | DCR is "supported out of the box" per Claude's docs and needs nothing from the user in the connector dialog — Client ID/Secret stay blank. CIMD would require hosting a metadata document on Claude's side (not applicable — Claude is the *client* here, not us) and manual entry defeats the point of this project. |
| OAuth state storage | **In-memory only** (`dict`s in the `oauth` module) | Simplest — no new file, schema, or volume. Accepted cost: a container restart/redeploy clears registered clients and in-flight codes, so the connector may need a one-time reconnect after a deploy. Given deploys are infrequent and this is single-user, not worth a SQLite file for durability. |
| Access token identity | **The existing `OPENBRAIN_TOKEN` itself**, returned verbatim by `/token` | Zero changes to `BearerAuthMiddleware`'s actual check — the OAuth flow only decides *whether* to hand back the token, never mints a parallel one. Accepted cost: revoking claude.ai's access later means rotating `OPENBRAIN_TOKEN` everywhere (same as revoking any existing client today), not a claude.ai-only revocation. |
| Token lifetime | **Non-expiring** — no `expires_in`, no refresh token, `offline_access` not advertised | The underlying secret doesn't expire today either; adding refresh-token machinery would be pure unused complexity. |
| Gating `/authorize` | **Reuse the GUI's Traefik basic-auth** (`GUI_BASIC_AUTH_USERS`) on a new path-scoped router | Matches an existing, working pattern in this repo exactly. Reaching `/authorize` at all already proves it's Stephan — no separate consent-screen UI needed inside the app. Rejected: a new dedicated credential (extra secret to manage for marginal isolation gain in a single-user setup); an in-app login form (new code for a solved problem). |
| PKCE | **Mandatory `S256`** on every `/authorize` request | Required by the MCP authorization spec regardless of client type; trivial to implement (SHA-256 + base64url) and closes off code-interception risk even though this is a personal, low-traffic server. |

## 4. Design

### 4.1 New module: `openbrain-mcp/app/oauth.py`

In-memory state (module-level, process lifetime only):

```python
import secrets, time, hashlib, base64

_clients: dict[str, dict] = {}       # client_id -> {"redirect_uris": [...], "created_at": float}
_auth_codes: dict[str, dict] = {}    # code -> {"client_id", "code_challenge", "redirect_uri", "expires_at"}

CODE_TTL_SECONDS = 60
```

Helper:

```python
def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(computed, code_challenge)
```

Route handlers (registered as Starlette `Route`s from `build_app()`):

**`GET /.well-known/oauth-authorization-server`** (RFC 8414) — static JSON,
built from `OPENBRAIN_HOST` (added to `config.py`, read from the env var
already set in compose for Traefik's `Host()` rule):

```python
{
  "issuer": f"https://{OPENBRAIN_HOST}",
  "authorization_endpoint": f"https://{OPENBRAIN_HOST}/authorize",
  "token_endpoint": f"https://{OPENBRAIN_HOST}/token",
  "registration_endpoint": f"https://{OPENBRAIN_HOST}/register",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
}
```

**`GET /.well-known/oauth-protected-resource`** (RFC 9728):

```python
{
  "resource": f"https://{OPENBRAIN_HOST}/mcp",
  "authorization_servers": [f"https://{OPENBRAIN_HOST}"],
}
```

**`POST /register`** (RFC 7591 DCR) — accepts client metadata JSON, but
**only accepts `redirect_uris` that are a subset of a hardcoded allowlist**
containing the one real claude.ai callback URL
(`https://claude.ai/api/mcp/auth_callback`, per Anthropic's connector docs —
the same URI for all hosted Claude surfaces: web, Desktop, mobile, Cowork):

```python
ALLOWED_REDIRECT_URIS = {"https://claude.ai/api/mcp/auth_callback"}


async def register(request: Request) -> JSONResponse:
    body = await request.json()
    redirect_uris = body.get("redirect_uris") or []
    if not redirect_uris or not set(redirect_uris) <= ALLOWED_REDIRECT_URIS:
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

**Why this allowlist exists (added after a code-quality review during
implementation, not part of the original brainstorm):** `/register` is
deliberately unauthenticated — that's required for DCR to work at all,
since a client hasn't obtained a token yet. Without this allowlist, anyone
could call `/register` with their *own* server as `redirect_uri`, then get
Stephan to click a crafted `/authorize?client_id=<theirs>&redirect_uri=<theirs>&...`
link. If his browser had a cached Traefik basic-auth session for this host
(e.g. from recently using `openbrain-gui`), that request would complete
*silently* — no visible prompt, since `/authorize` has no consent screen —
and 302 a valid one-time code straight to the attacker's server. Once
`/token` exists (§4.1 below), that code redeems for `OPENBRAIN_TOKEN`
itself: the real, shared production secret used by Hermes, Claude Code,
Claude Desktop, and the GUI. That's a materially bigger threat than the one
this design originally scoped for (§3: "a stranger who discovers the
hostname" — not "a stranger who gets one link clicked"). Pinning
`/register` to the one real callback URL closes this completely: an
attacker can still call `/register`, but the resulting client's `redirect_uri`
can now only ever be claude.ai's own real callback, so `/authorize` can
never hand a code to infrastructure the attacker controls. No consent
screen or other mitigation is needed on top of this for the current
single-client (claude.ai only) scope.

**`MAX_REGISTERED_CLIENTS` cap (added after a whole-branch review, same
reasoning as the allowlist above):** the redirect_uri allowlist stops a
registered client from being *useful* to an attacker, but `/register` is
still unauthenticated and `_clients` is still an unbounded, in-memory dict —
nothing stops repeated calls from growing it indefinitely, a memory-
exhaustion DoS against the whole `openbrain-mcp` process (which also serves
`/mcp` for every other client, not just the OAuth path). `register()` now
rejects past 1000 entries with `429 {"error": "temporarily_unavailable"}` —
a hard backstop, not a rate limit; a single legitimate client re-registers
at most a handful of times.

**`GET /authorize`** — reachable only through the new Traefik-gated router
(§4.3). Validates `client_id` and that `redirect_uri` is one the client
registered, requires `code_challenge` + `code_challenge_method=S256`, issues
a one-time code, redirects immediately (no consent page — Traefik's
basic-auth prompt *is* the consent step):

```python
async def authorize(request: Request) -> RedirectResponse | JSONResponse:
    q = request.query_params
    client = _clients.get(q.get("client_id", ""))
    redirect_uri = q.get("redirect_uri", "")
    if not client or redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        return JSONResponse({"error": "invalid_request",
                              "error_description": "PKCE S256 required"}, status_code=400)
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

**`POST /token`** — form-encoded per RFC 6749 §4.1.3:

```python
async def token(request: Request) -> JSONResponse:
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    code = form.get("code")
    client_id = form.get("client_id")
    code_verifier = form.get("code_verifier")
    grant_type = form.get("grant_type")
    redirect_uri = form.get("redirect_uri")

    if not all(isinstance(v, str) for v in (code, client_id, code_verifier, redirect_uri)):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if grant_type != "authorization_code":
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    entry = _auth_codes.pop(code, None)   # single-use: pop, not get
    if not entry or entry["expires_at"] < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["client_id"] != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if entry["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _verify_pkce(code_verifier, entry["code_challenge"]):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse({
        "access_token": OPENBRAIN_TOKEN,
        "token_type": "Bearer",
    })
```

(No `expires_in` field → per RFC 6749, the client should treat the token as
not expiring. No `refresh_token`.)

**Hardened after a code-quality review during implementation (not part of
the original brainstorm):** the first version of this handler read `code`
via `form.get("code", "")` and called `_auth_codes.pop(...)` immediately,
before validating anything else. Two problems: (1) `await request.form()`
was unguarded, so a malformed multipart body (bad boundary, or a field like
`code_verifier` submitted as a file rather than plain text) raised an
unhandled exception — a 500 instead of `invalid_grant` — and since this pop
happened first, such a request could permanently destroy a real, still-valid
one-time code before ever finishing validation, denying the legitimate
client mid-flow. (2) Per RFC 6749 §4.1.3, the token request must also
include `grant_type=authorization_code` and, since `/authorize` always
requires and stores a `redirect_uri`, the token request's `redirect_uri`
must match it — neither was checked. The fixed version above type-checks
every field and validates `grant_type` *before* touching `_auth_codes`, and
checks `redirect_uri` against the value captured at `/authorize` time.

### 4.2 Changes to `server.py`

Wire the new routes into `build_app()`, alongside the existing `/health`:

```python
from app.oauth import (
    well_known_auth_server, well_known_protected_resource,
    register, authorize, token,
)

def build_app() -> Starlette:
    app = mcp.streamable_http_app()
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.router.routes.append(Route("/.well-known/oauth-authorization-server",
                                    well_known_auth_server, methods=["GET"]))
    app.router.routes.append(Route("/.well-known/oauth-protected-resource",
                                    well_known_protected_resource, methods=["GET"]))
    app.router.routes.append(Route("/register", register, methods=["POST"]))
    app.router.routes.append(Route("/authorize", authorize, methods=["GET"]))
    app.router.routes.append(Route("/token", token, methods=["POST"]))
    app.add_middleware(BearerAuthMiddleware)
    return app
```

`BearerAuthMiddleware` gets two changes:

1. Exempt the new pre-auth endpoints (a client hasn't obtained the bearer
   token yet when it hits them) alongside the existing `/health` exemption:
   `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`,
   `/register`, `/authorize`, `/token`.
2. On the `401` it already returns for everything else, add the
   `WWW-Authenticate` header Claude's discovery flow needs:

```python
class BearerAuthMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {
        "/health",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/register", "/authorize", "/token",
    }

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        expected = f"Bearer {OPENBRAIN_TOKEN}"
        if not OPENBRAIN_TOKEN or request.headers.get("authorization") != expected:
            resource_meta = f"https://{OPENBRAIN_HOST}/.well-known/oauth-protected-resource"
            return JSONResponse(
                {"error": "unauthorized"}, status_code=401,
                headers={"WWW-Authenticate": f'Bearer resource_metadata="{resource_meta}"'},
            )
        return await call_next(request)
```

Note `/authorize` is in the middleware's exemption set (it needs no bearer
token — it's gated by Traefik basic-auth instead, one layer further out), but
it is **not** reachable at all unless a request passes that basic-auth
check, added next.

### 4.3 `openbrain-mcp/app/config.py`

Add `OPENBRAIN_HOST = os.environ.get("OPENBRAIN_HOST", "")`, read from the
same env var the compose file already sets for Traefik's `Host()` rule (it
isn't currently passed into the container's environment — this spec adds it
to the `environment:` block in §4.4). Used to build absolute URLs in the two
`.well-known` documents and the `WWW-Authenticate` header.

### 4.4 `deploy/docker-compose.openbrain.yml`

Two changes to the `openbrain-mcp` service:

1. Add `OPENBRAIN_HOST: ${OPENBRAIN_HOST}` to its `environment:` block (it's
   already in `.env`/`.env.example` for Traefik's own label, just not
   currently passed into the container).
2. Add a second, higher-priority Traefik router scoped to `/authorize`,
   reusing the GUI's existing basic-auth middleware:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.openbrain.rule=Host(`${OPENBRAIN_HOST}`)"
  - "traefik.http.routers.openbrain.entrypoints=websecure"
  - "traefik.http.routers.openbrain.tls.certresolver=letsencrypt"
  - "traefik.http.services.openbrain.loadbalancer.server.port=8080"
  - "traefik.http.routers.openbrain.priority=1"
  # Gate only the human-facing /authorize step with basic-auth, using the
  # same credentials as the GUI. Everything else on this host (/mcp, /token,
  # /register, /.well-known/*) stays reachable without it -- those are
  # machine-to-machine calls from Claude's backend, never a browser.
  - "traefik.http.routers.openbrain-authorize.rule=Host(`${OPENBRAIN_HOST}`) && Path(`/authorize`)"
  - "traefik.http.routers.openbrain-authorize.entrypoints=websecure"
  - "traefik.http.routers.openbrain-authorize.tls.certresolver=letsencrypt"
  - "traefik.http.routers.openbrain-authorize.priority=100"
  - "traefik.http.middlewares.openbrain-authorize-auth.basicauth.users=${GUI_BASIC_AUTH_USERS}"
  - "traefik.http.routers.openbrain-authorize.middlewares=openbrain-authorize-auth"
  - "traefik.http.routers.openbrain-authorize.service=openbrain"
```

(Same `GUI_BASIC_AUTH_USERS` credentials as the GUI, but the basicauth
middleware is defined here on `openbrain-mcp`'s own labels
(`openbrain-authorize-auth`), not by referencing `openbrain-gui`'s
`openbrain-gui-auth` middleware by name — **added after a whole-branch
review, not part of the original brainstorm**: Traefik's Docker provider
resolves a middleware from whichever container's labels define it, and if
that container (`openbrain-gui`) is ever stopped or rebuilt independently,
Traefik drops any router referencing its now-undefined middleware —
including `openbrain-authorize`, falling through to the unprotected plain
`openbrain` router. Defining the middleware on `openbrain-mcp` itself makes
`/authorize`'s protection depend only on this container's own lifecycle.
The new router still points at the *existing* `openbrain` service rather
than declaring a duplicate one — Traefik services are referenced by name
across routers in the same provider independently of middlewares.

**Both routers need an explicit `priority` (added after a review-driven fix
during implementation, not part of the original brainstorm):** Traefik
compares priorities numerically regardless of whether a value is explicit or
automatically computed from rule length, and it does not treat "more
specific rule" as inherently higher-priority on its own. An earlier version
of this design set only `openbrain-authorize`'s priority (`10`), leaving
`openbrain` unset — but an unset priority isn't "low," it's automatically
computed as that router's rule length in characters, commonly 35-45 for a
real hostname. Since 35-45 > 10, the plain, unprotected `Host()`-only router
would have silently outranked the basic-auth-gated one for every request to
`/authorize`, defeating this section's entire purpose without any error or
warning. Setting both explicitly (`100` vs. `1`) removes any dependency on
hostname length or Traefik's automatic/explicit tie-breaking rules.)

No new environment variables beyond passing through the existing
`OPENBRAIN_HOST`, no new secrets, no new containers, no schema/DB changes.

### 4.5 Data flow (end to end)

1. Stephan pastes `https://brain.srv1608402.hstgr.cloud/mcp` into claude.ai's
   "Add custom connector" (web or mobile), leaves Client ID/Secret blank.
2. Claude calls `/mcp`, gets `401` with the new `WWW-Authenticate` header →
   fetches `/.well-known/oauth-protected-resource` → finds the authorization
   server is the same host → fetches `/.well-known/oauth-authorization-server`
   → sees `registration_endpoint` → `POST /register` → gets a `client_id`
   back, no user interaction.
3. Claude opens `/authorize?client_id=...&redirect_uri=https://claude.ai/api/mcp/auth_callback&code_challenge=...&code_challenge_method=S256&state=...` in a browser (or the mobile app's in-app browser). Traefik's basic-auth prompts for the existing GUI credentials. On success, the request reaches the `authorize` handler, which validates and redirects straight to `redirect_uri?code=...&state=...`.
4. Claude's backend `POST /token`s with the code, `client_id`, and PKCE
   `code_verifier`. The handler verifies and returns `OPENBRAIN_TOKEN` as
   `access_token`.
5. Every subsequent `/mcp` call from claude.ai carries
   `Authorization: Bearer <OPENBRAIN_TOKEN>` — indistinguishable at the
   middleware from Claude Code, Claude Desktop, or the GUI backend.

### 4.6 Error handling

| Condition | Response |
|---|---|
| `/register` missing/empty `redirect_uris`, or any `redirect_uris` entry outside `ALLOWED_REDIRECT_URIS` | `400 {"error": "invalid_client_metadata"}` |
| `/authorize` unknown `client_id`, or `redirect_uri` not registered for it | `400 {"error": "invalid_client"}` |
| `/authorize` missing/wrong PKCE method | `400 {"error": "invalid_request", ...}` |
| `/token` unknown, expired, or already-used code | `400 {"error": "invalid_grant"}` (RFC 6749-compliant code — Claude's docs specifically call out needing this exact code, not a custom one, for its refresh/retry logic to behave) |
| `/token` `client_id` mismatch or failed PKCE verification | `400 {"error": "invalid_grant"}` |
| Container restart between steps 2–4 | `_clients`/`_auth_codes` are empty again; `/authorize` or `/token` fail as "unknown client"/"invalid_grant" respectively, and claude.ai surfaces a connection error. Recovery is reconnecting the connector, which re-runs the whole flow from step 2 — accepted per §3. |

## 5. Non-goals (this iteration)

- No change to how Claude Code, Claude Desktop, or `openbrain-gui` connect —
  they keep using the static `OPENBRAIN_TOKEN` directly, unaffected by any of
  this.
- No multi-user / multi-tenant support, no per-client scoping or revocation
  UI, no refresh tokens.
- No submission to Anthropic's connector directory (`mcp-review@anthropic.com`)
  — this is a personal connector added by URL, not a listed one, so none of
  the directory-review auth types (`static_headers`, `oauth_anthropic_creds`,
  `custom_connection`) apply or are needed.
- No consent-screen UI inside the app — Traefik's basic-auth prompt is the
  entire "is this really Stephan" check.
- No durability for OAuth state across restarts (explicitly accepted in §3).

## 6. Success criteria

- Adding `https://brain.srv1608402.hstgr.cloud/mcp` as a custom connector in
  claude.ai on the phone, with Client ID/Secret left blank, completes without
  error: prompts once for the existing basic-auth credentials, then shows the
  connector as connected.
- A real tool call from claude.ai (e.g. `stats` or `search`) against
  `openbrain-mcp` returns real data.
- Existing clients (Claude Code, Claude Desktop, `openbrain-gui`, Hermes)
  continue to work completely unchanged — verified by re-running one call
  from each after deploying.
- New unit tests (below) pass; full existing test suite has no regressions.

## 7. Test plan

New tests in `openbrain-mcp/tests/test_oauth.py`:

1. `test_well_known_authorization_server_metadata_shape` — required RFC 8414
   fields present, URLs built from `OPENBRAIN_HOST`.
2. `test_well_known_protected_resource_metadata_shape` — RFC 9728 fields
   present, `resource` matches the `/mcp` URL.
3. `test_register_returns_client_id_for_valid_metadata` — POST with the
   allowed `redirect_uris` → 200, a `client_id`, and that client is later
   usable at `/authorize`.
4. `test_register_rejects_missing_redirect_uris` → 400.
4a. `test_register_rejects_redirect_uri_outside_allowlist` — POST with a
   well-formed but non-allowlisted `redirect_uris` (e.g.
   `https://attacker.example/callback`) → 400 `invalid_client_metadata`.
5. `test_full_authorize_token_flow_with_valid_pkce` — register → authorize
   (valid PKCE challenge, matching registered redirect_uri) → follow the
   redirect's `code` → token → asserts `access_token == OPENBRAIN_TOKEN`.
6. `test_authorize_rejects_unknown_client_id` → 400.
7. `test_authorize_rejects_unregistered_redirect_uri` → 400.
8. `test_authorize_rejects_missing_pkce` → 400.
9. `test_token_rejects_expired_code` (freeze/monkeypatch `time.time()` past
   `CODE_TTL_SECONDS`) → 400 `invalid_grant`.
10. `test_token_rejects_replayed_code` (use the same code twice) → second
    call 400 `invalid_grant`.
11. `test_token_rejects_wrong_code_verifier` → 400 `invalid_grant`.
12. `test_bearer_middleware_still_rejects_mcp_without_token` — existing
    behavior unchanged, now also asserts the `WWW-Authenticate` header is
    present on that `401`.

Manual smoke test (after deploying to the VPS): actually add the connector
in claude.ai on the phone, confirm the basic-auth prompt appears, confirm
connection succeeds, run one real tool call and confirm it returns live
data — then confirm Claude Code/Desktop and the GUI still work unchanged.

## 8. Implementation outline (to be expanded into a plan)

1. Add `OPENBRAIN_HOST` to `app/config.py`.
2. Write `app/oauth.py` (stores, PKCE helper, five handlers) with unit tests
   1–11 above, TDD.
3. Update `BearerAuthMiddleware` in `server.py` (exemptions +
   `WWW-Authenticate` header) and wire the five new routes into `build_app()`;
   test 12.
4. Update `deploy/docker-compose.openbrain.yml` (`OPENBRAIN_HOST` env var
   passthrough, new `/authorize` router with its own basic-auth middleware
   using the same `GUI_BASIC_AUTH_USERS` credentials as the GUI).
5. Deploy to the VPS, run the manual smoke test above.
6. Update `README.md`'s OpenBrain section with a short note on the new OAuth
   path for personal claude.ai connectors, alongside the existing Claude
   Code/Desktop setup notes.
