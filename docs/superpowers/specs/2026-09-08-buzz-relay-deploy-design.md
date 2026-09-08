# Buzz relay — deployment design

**Date:** 2026-09-08
**Status:** approved, ready for implementation plan
**Owner:** Stephan Schipal

## 1. Goal

Run a self-hosted **Buzz** relay (Block's Nostr-based collaboration platform —
team chat, code repos, workflows, AI agents in shared rooms) on the existing
Hostinger VPS `srv1608402`, integrated with the box's existing Traefik ingress,
managed the same git-from-checkout way as the openbrain and stripe stacks.

Success criteria:

- `https://buzz.srv1608402.hstgr.cloud` serves the Buzz web UI and its NIP-11
  relay document over a valid Let's Encrypt certificate.
- `wss://buzz.srv1608402.hstgr.cloud` completes a WebSocket handshake.
- The Buzz desktop app can add the relay and the configured owner identity is
  recognised as owner/admin.
- No regression to any existing service (Traefik, Hermes agent, openbrain MCP +
  GUI + DB, stripe MCP).
- Deployment, secrets, and backup/restore are documented in `BuzzDocu.md`.

## 2. Context — the target VPS (inspected 2026-09-08)

| Resource | State |
| --- | --- |
| RAM | 15 GiB total, ~12 GiB available (stack uses ~3.4 GiB) |
| Disk | 193 GB, ~173 GB free (`/`, also backs `/var/lib/docker`) |
| CPU | 4 vCPU, load avg ~0.12 |
| Ports 80/443 | `traefik-traefik-1`, `network_mode: host` |

Traefik launch (from `/docker/traefik/docker-compose.yml`):

- `--providers.docker=true --providers.docker.exposedbydefault=false`
- `--providers.file.directory=/etc/traefik/dynamic` (bind `/docker/traefik/dynamic`)
- entrypoints `web :80` / `websecure :443`, global `web -> websecure` redirect
- `certificatesresolvers.letsencrypt.acme.httpchallenge` on the `web` entrypoint,
  storage `/letsencrypt/acme.json`

Because Traefik runs host-mode, it reaches service containers by their **bridge
IP directly** — no shared Traefik network is needed. This is already proven by
the openbrain stack (`deploy/docker-compose.openbrain.yml`), whose services carry
only `traefik.*` labels and sit on their own bridge networks. The file provider
is also in use (`/docker/traefik/dynamic/voice.yml` routes `…/voice` to a
container IP).

Compose projects on the box, all deployed from `/root/HermesPlusOpenbrain`
except the Hermes agent:

- `traefik`  → `/docker/traefik/docker-compose.yml`
- `deploy`   → `deploy/docker-compose.openbrain.yml`
- `stripe`   → `deploy/docker-compose.stripe.yml` (own project name `stripe`)
- `hermes-agent-7qpk` → `/docker/hermes-agent-7qpk/docker-compose.yml`

DNS: `*.srv1608402.hstgr.cloud` is a wildcard resolving to the VPS (used today by
`brain.` and `gui.`). No DNS record work is required for a new `buzz.` subdomain.

## 3. Upstream Buzz (github.com/block/buzz, `deploy/compose/`)

`compose.yml` — project name `buzz-prod`:

| Service | Image | Notes |
| --- | --- | --- |
| `relay` | `${BUZZ_IMAGE:-ghcr.io/block/buzz:main}` | single Rust binary: WS + REST + web UI; `BUZZ_BIND_ADDR=0.0.0.0:3000`, health `:8080`, metrics `:9102`; publishes `${BUZZ_HTTP_PORT:-3000}:3000` |
| `postgres` | `postgres:17-alpine` | volume `buzz-postgres-data` |
| `redis` | `redis:7-alpine` | `--appendonly yes --requirepass …`; volume `buzz-redis-data` |
| `minio` | `minio/minio:RELEASE.2025-09-07T16-13-09Z` | S3 for media + git objects; volume `buzz-minio-data` |
| `minio-init` | `minio/mc:…` | one-shot: creates the bucket, `restart: "no"` |

All on bridge network `buzz-net`. TLS is **not** in this file — it lives in the
optional overlay `compose.caddy.yml` (`BUZZ_COMPOSE_TLS=true`), which adds a
`caddy` container binding host `80`/`443`, whose entire config is
`{$BUZZ_DOMAIN} { reverse_proxy relay:3000 }`.

`.env.example` production defaults worth keeping: `BUZZ_REQUIRE_AUTH_TOKEN=true`,
`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true` (closed relay — only members join),
`BUZZ_AUTO_MIGRATE=true`. Server release tags: latest is `v0.5.2`
(the `desktop-v*` tags are the desktop app, not the relay image).

Required identity / secret env:

| Var | Source | Rotatable? |
| --- | --- | --- |
| `RELAY_OWNER_PUBKEY` | 64-hex form of the owner's Nostr `npub` (created in the Buzz desktop app) | no — this is the permanent owner account |
| `BUZZ_RELAY_PRIVATE_KEY` | `openssl rand -hex 32`, generated once | no — changing it changes the relay's advertised identity |
| `BUZZ_GIT_HOOK_HMAC_SECRET` | `openssl rand -hex 32` | no |
| `POSTGRES_PASSWORD` | random, URL-safe (`openssl rand -hex 32`) — interpolated into `DATABASE_URL` | no (would orphan the DB) |
| `REDIS_PASSWORD` | random | on redis flush only |
| `BUZZ_S3_ACCESS_KEY` / `BUZZ_S3_SECRET_KEY` | random; also MinIO root creds | no (would orphan the bucket) |

URL-derived vars all point at the public host: `BUZZ_DOMAIN`,
`RELAY_URL=wss://…`, `BUZZ_MEDIA_BASE_URL=https://…/media`,
`BUZZ_MEDIA_SERVER_DOMAIN`, `BUZZ_CORS_ORIGINS=https://…`.

## 4. Design

### 4.1 Approach — git-managed compose (mirrors openbrain / stripe)

Rejected alternatives:

- **Hostinger Docker Manager one-click.** Its template bundles an ingress that
  expects 80/443 → collides with Traefik; post-install we would be hand-editing
  Hostinger's generated compose, which Docker Manager can revert. Opaque, and off
  the repo's git-from-checkout pattern.
- **Separate VPS.** Unnecessary — 12 GiB RAM / 173 GB disk headroom. Adds cost.
- **Hostinger "Change OS" Buzz template.** Reimages the VPS — destroys the entire
  Hermes/openbrain/stripe stack. Never.

### 4.2 New files in this repo

- `deploy/docker-compose.buzz.yml` — a **vendored copy** of upstream
  `deploy/compose/compose.yml`, with these deltas:
  - `name: buzz` (own project name, like `stripe`, so a `--remove-orphans` on
    another stack can't tear it down; upstream's `buzz-prod` is not special).
  - `relay` port mapping changed from `${BUZZ_HTTP_PORT:-3000}:3000` to
    `127.0.0.1:${BUZZ_HTTP_PORT:-3000}:3000` — loopback only, for debugging; the
    public path is Traefik.
  - `relay` gains `traefik.*` labels (see 4.3). The Caddy overlay is **not**
    vendored and `BUZZ_COMPOSE_TLS` is never set.
  - Network renamed `buzz-net` → `buzz_net` for house style; still an internal
    bridge, nothing external.
  - Volume names kept (`buzz-postgres-data` etc.) so they read the same as
    upstream docs.
  - Image pinned via `BUZZ_IMAGE` in `.env` to a release tag, not `:main`.
  - Header comment explaining it is vendored, its upstream path + pinned commit,
    and the deltas — so a future upgrade is a visible diff.
- `deploy/.env.example` — appended `# --- buzz (docker-compose.buzz.yml)` block
  with every var from 3 above, each with a generate-command comment. `$` in any
  value doc'd as needing `$$` for compose interpolation (as the GUI basic-auth
  line already does).
- `BuzzDocu.md` (repo root, alongside `TwilioDocu.md`, `CaptureBotDocu.md`,
  `stripe-mcp/DEPLOY.md`) — operational reference: what Buzz is, the container
  map, first-deploy runbook, how to add/remove members
  (`docker compose … exec relay /usr/local/bin/buzz-admin …`), upgrade
  procedure (bump `BUZZ_IMAGE`, `up -d --wait`), and the backup/restore
  procedure from 4.5.
- `README.md` — one row in the services table + a link to `BuzzDocu.md`.

### 4.3 Ingress

`relay` service labels (mirrors the openbrain-mcp label block exactly):

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.buzz.rule=Host(`${BUZZ_DOMAIN}`)"
  - "traefik.http.routers.buzz.entrypoints=websecure"
  - "traefik.http.routers.buzz.tls.certresolver=letsencrypt"
  - "traefik.http.services.buzz.loadbalancer.server.port=3000"
  - "traefik.docker.network=buzz_buzz_net"
```

`traefik.docker.network` is set explicitly (openbrain omits it, but openbrain-mcp
is multi-homed and happens to resolve; `relay` is single-network so the value is
`<project>_<network>` = `buzz_buzz_net`). WebSocket upgrade needs no special
Traefik config — it is transparent on `websecure`. The global `web → websecure`
redirect and the ACME http-challenge on `:80` are already in place; a `buzz.`
cert issues on first request exactly as `brain.` / `gui.` did.

`BUZZ_DOMAIN` is not a secret but still lives in `deploy/.env` (set to
`buzz.srv1608402.hstgr.cloud`); `.env.example` carries only a placeholder.

### 4.4 Relay configuration

Closed relay, owner-gated (upstream production defaults):

- `BUZZ_REQUIRE_AUTH_TOKEN=true`
- `BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`
- `BUZZ_AUTO_MIGRATE=true` (first boot runs embedded migrations on the empty DB)
- `RELAY_OWNER_PUBKEY=<hex>` — cannot be removed as a member.

Members are added later with `buzz-admin add-member --pubkey <npub|hex>`
(documented; not part of the deploy).

### 4.5 Backups (v1 = documented procedure + cron snippet, not off-box automation)

State that must be captured from one maintenance window:

- `deploy/.env` (the secrets — store in the password manager, not just on disk)
- Postgres: `docker compose … exec -T postgres pg_dump -U buzz buzz | gzip`
- MinIO bucket: `docker run --rm … minio/mc mirror` **or** tar of the
  `buzz-minio-data` volume while `minio` is stopped
- `buzz-git-data` volume (`BUZZ_GIT_REPO_PATH=/data/git`)
- `buzz-redis-data` is derivable (pub/sub + cache) — nice-to-have, not critical

`BuzzDocu.md` ships a `/root/buzz-backup.sh` + a daily `cron` line writing to
`/root/backups/buzz/` with 7-day rotation. Off-box shipping (rsync/S3) is called
out as a follow-up, consistent with the rest of the stack (which also has no
off-box automation today).

### 4.6 Firewall

No change. 80/443 are already open and owned by Traefik. Buzz publishes only
`127.0.0.1:3000`. Postgres/Redis/MinIO are not published at all.

## 5. Out of scope (phase 2)

- **Moderation dashboard** — `BUZZ_ADMIN_HOST=admin.buzz.srv1608402.hstgr.cloud`
  with `BUZZ_ADMIN_AUTH=nip98`, fronted by Traefik (optionally + basic-auth like
  the openbrain GUI). Deferred to keep v1 to the relay itself.
- **Off-box / automated backups.**
- **Prometheus / metrics scraping** of `relay:9102` (upstream `compose.dev.yml`
  wires a Prometheus; not needed for v1).
- **Operator/community provisioning API** (`RELAY_OPERATOR_API_ORIGIN`).

## 6. Risks

| Risk | Mitigation |
| --- | --- |
| Vendored compose drifts from upstream | Header comment records upstream path + pinned commit; upgrades are a visible diff; `BUZZ_IMAGE` pinned |
| `RELAY_OWNER_PUBKEY` / `BUZZ_RELAY_PRIVATE_KEY` lost | Backed up to password manager before first `up`; both are documented as non-rotatable |
| ACME rate limit if the route flaps during setup | Bring the stack up healthy first, add the Traefik labels only once `relay` is confirmed serving on `127.0.0.1:3000` |
| MinIO image tag pinned to a 2025 release | Acceptable — upstream's choice; revisit at first upgrade |
| Resource growth (media in MinIO, events in PG) | 173 GB free; backup script surfaces size; disk alert is a phase-2 item |

## 7. Verification (post-deploy)

1. `docker compose -f deploy/docker-compose.buzz.yml ps` — all `healthy`,
   `minio-init` `Exited (0)`.
2. `curl -sI https://buzz.srv1608402.hstgr.cloud` — `200`, valid LE cert.
3. `curl -s -H 'Accept: application/nostr+json' https://buzz.srv1608402.hstgr.cloud`
   — NIP-11 JSON, `pubkey` matches the relay key, owner listed.
4. WebSocket: `websocat wss://buzz.srv1608402.hstgr.cloud` (or a Nostr client)
   completes the handshake.
5. Buzz desktop app → add relay → owner identity shows as owner/admin.
6. Regression: `brain.`, `gui.`, the Hermes agent, and stripe MCP all still
   respond; `docker ps` shows no restarts.
