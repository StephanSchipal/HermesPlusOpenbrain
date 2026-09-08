# Buzz relay deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a self-hosted Buzz relay (Block's Nostr collaboration platform) on the Hostinger VPS `srv1608402`, behind the existing Traefik, managed as git-checkout compose like the openbrain and stripe stacks.

**Architecture:** Vendor Block's upstream `deploy/compose/compose.yml` into `deploy/docker-compose.buzz.yml` (own project name `buzz`), swap its bundled Caddy for Traefik labels on the `relay` service, prefix its Postgres/Redis env keys so they don't collide with openbrain's in the shared `deploy/.env`, and expose it at `wss://buzz.srv1608402.hstgr.cloud` via the wildcard DNS that already points at the box.

**Tech Stack:** Docker Compose, Traefik v3 (host network, Docker + file providers, Let's Encrypt http-challenge), Buzz relay `ghcr.io/block/buzz`, PostgreSQL 17, Redis 7, MinIO.

**Design doc:** [`docs/superpowers/specs/2026-09-08-buzz-relay-deploy-design.md`](../specs/2026-09-08-buzz-relay-deploy-design.md)

**Upstream reference:** `github.com/block/buzz`, `deploy/compose/compose.yml` at commit `7012d86d52fd188b27c7beedeaa132d9c1f61fa8` (repo main `3c7f288` on 2026-09-05). Latest relay image release tag: `v0.5.2`.

---

## File structure

| File | Responsibility | Action |
| --- | --- | --- |
| `deploy/docker-compose.buzz.yml` | The Buzz stack: relay + postgres + redis + minio + minio-init, project `buzz`, Traefik labels on relay | Create |
| `deploy/.env.example` | Append a documented `# --- buzz` block with every `BUZZ_*` var | Modify |
| `BuzzDocu.md` | Operational reference: what Buzz is, container map, first-deploy runbook, member admin, upgrade, backup/restore | Create |
| `README.md` | One row in the services table + link to `BuzzDocu.md` | Modify |
| `scripts/buzz-backup.sh` | Nightly `pg_dump` + minio/git volume tar to `/root/backups/buzz/`, 7-day rotation | Create |
| `deploy/.env` (VPS only, never committed) | Real secrets | Modify on VPS |

No application code, no unit-test suite — this is infrastructure. "Tests" here are `docker compose config` validation locally and HTTP/WS/regression probes on the VPS.

---

## Task 1: Vendor and adapt the Buzz compose file

**Files:**
- Create: `deploy/docker-compose.buzz.yml`

- [ ] **Step 1: Fetch the pinned upstream compose for reference**

Run:
```bash
gh api repos/block/buzz/contents/deploy/compose/compose.yml?ref=7012d86d52fd188b27c7beedeaa132d9c1f61fa8 --jq '.content' | base64 -d > /tmp/buzz-upstream-compose.yml
cat /tmp/buzz-upstream-compose.yml
```
Expected: the upstream file with services `relay`, `postgres`, `redis`, `minio`, `minio-init`, project `name: buzz-prod`, network `buzz-net`, relay publishing `"${BUZZ_HTTP_PORT:-3000}:3000"`, relay using `env_file: - .env`.

- [ ] **Step 2: Write `deploy/docker-compose.buzz.yml`**

This is the vendored file with exactly six deltas from upstream (listed in the header). Write it verbatim:

```yaml
# deploy/docker-compose.buzz.yml
#
# Self-hosted Buzz relay (Block's Nostr collaboration platform: team chat, code
# repos, workflows, AI agents in shared rooms).
#
# VENDORED from github.com/block/buzz : deploy/compose/compose.yml
#   pinned commit: 7012d86d52fd188b27c7beedeaa132d9c1f61fa8
# Re-vendor = re-diff. Deltas from upstream (keep this list current):
#   1. name: buzz-prod  ->  name: buzz         (own project, cf. docker-compose.stripe.yml)
#   2. network buzz-net  ->  buzz_net          (house style)
#   3. relay ports "${BUZZ_HTTP_PORT:-3000}:3000"
#        ->  "127.0.0.1:${BUZZ_HTTP_PORT:-3000}:3000"   (loopback only; public path is Traefik)
#   4. relay: dropped `env_file: - .env`; every var listed under `environment:` so this
#      container never inherits the openbrain/stripe secrets in the shared deploy/.env
#   5. relay: added traefik.* labels; upstream compose.caddy.yml is NOT vendored
#   6. Postgres/Redis .env keys prefixed BUZZ_ (POSTGRES_PASSWORD etc. are already
#      taken by the openbrain stack in the shared deploy/.env). In-container var
#      names are left as upstream expects.
#   7. BUZZ_IMAGE default pinned to a release tag, not :main
#
# Deploy:  cd deploy && docker compose -f docker-compose.buzz.yml up -d --wait
# Ops ref: ../BuzzDocu.md

name: buzz

services:
  relay:
    image: ${BUZZ_IMAGE:-ghcr.io/block/buzz:v0.5.2}
    environment:
      BUZZ_BIND_ADDR: 0.0.0.0:3000
      BUZZ_HEALTH_PORT: "8080"
      BUZZ_METRICS_PORT: "9102"
      DATABASE_URL: postgres://${BUZZ_POSTGRES_USER:-buzz}:${BUZZ_POSTGRES_PASSWORD:?set BUZZ_POSTGRES_PASSWORD}@postgres:5432/${BUZZ_POSTGRES_DB:-buzz}
      REDIS_URL: redis://:${BUZZ_REDIS_PASSWORD:?set BUZZ_REDIS_PASSWORD}@redis:6379
      BUZZ_S3_ENDPOINT: http://minio:9000
      BUZZ_S3_ADDRESSING_STYLE: path
      BUZZ_S3_ACCESS_KEY: ${BUZZ_S3_ACCESS_KEY:?set BUZZ_S3_ACCESS_KEY}
      BUZZ_S3_SECRET_KEY: ${BUZZ_S3_SECRET_KEY:?set BUZZ_S3_SECRET_KEY}
      BUZZ_S3_BUCKET: ${BUZZ_S3_BUCKET:-buzz-media}
      BUZZ_GIT_REPO_PATH: /data/git
      BUZZ_AUTO_MIGRATE: ${BUZZ_AUTO_MIGRATE:-true}
      BUZZ_GIT_CONFORMANCE_PROBE: ${BUZZ_GIT_CONFORMANCE_PROBE:-true}
      RELAY_OWNER_PUBKEY: ${RELAY_OWNER_PUBKEY:?set RELAY_OWNER_PUBKEY}
      BUZZ_RELAY_PRIVATE_KEY: ${BUZZ_RELAY_PRIVATE_KEY:?set BUZZ_RELAY_PRIVATE_KEY}
      BUZZ_GIT_HOOK_HMAC_SECRET: ${BUZZ_GIT_HOOK_HMAC_SECRET:?set BUZZ_GIT_HOOK_HMAC_SECRET}
      BUZZ_REQUIRE_AUTH_TOKEN: ${BUZZ_REQUIRE_AUTH_TOKEN:-true}
      BUZZ_REQUIRE_RELAY_MEMBERSHIP: ${BUZZ_REQUIRE_RELAY_MEMBERSHIP:-true}
      BUZZ_ALLOW_NIP_OA_AUTH: ${BUZZ_ALLOW_NIP_OA_AUTH:-true}
      BUZZ_DOMAIN: ${BUZZ_DOMAIN:?set BUZZ_DOMAIN}
      RELAY_URL: wss://${BUZZ_DOMAIN:?set BUZZ_DOMAIN}
      BUZZ_MEDIA_BASE_URL: https://${BUZZ_DOMAIN:?set BUZZ_DOMAIN}/media
      BUZZ_MEDIA_SERVER_DOMAIN: ${BUZZ_DOMAIN:?set BUZZ_DOMAIN}
      BUZZ_CORS_ORIGINS: https://${BUZZ_DOMAIN:?set BUZZ_DOMAIN}
      RUST_LOG: ${BUZZ_RUST_LOG:-buzz_relay=info,buzz_db=info,buzz_auth=info,buzz_pubsub=info,tower_http=info}
    ports:
      - "127.0.0.1:${BUZZ_HTTP_PORT:-3000}:3000"
    volumes:
      - buzz-git-data:/data/git
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
      minio-init:
        condition: service_completed_successfully
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "bash -ec 'exec 3<>/dev/tcp/127.0.0.1/8080; printf \"GET /_readiness HTTP/1.1\\r\\nHost: 127.0.0.1\\r\\nConnection: close\\r\\n\\r\\n\" >&3; grep -q \"200 OK\" <&3'",
        ]
      interval: 10s
      timeout: 3s
      retries: 12
      start_period: 30s
    restart: unless-stopped
    networks:
      - buzz_net
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.buzz.rule=Host(`${BUZZ_DOMAIN}`)"
      - "traefik.http.routers.buzz.entrypoints=websecure"
      - "traefik.http.routers.buzz.tls.certresolver=letsencrypt"
      - "traefik.http.services.buzz.loadbalancer.server.port=3000"
      - "traefik.docker.network=buzz_buzz_net"

  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: ${BUZZ_POSTGRES_DB:-buzz}
      POSTGRES_USER: ${BUZZ_POSTGRES_USER:-buzz}
      POSTGRES_PASSWORD: ${BUZZ_POSTGRES_PASSWORD:?set BUZZ_POSTGRES_PASSWORD}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - buzz-postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 12
      start_period: 10s
    restart: unless-stopped
    networks:
      - buzz_net

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes", "--requirepass", "${BUZZ_REDIS_PASSWORD:?set BUZZ_REDIS_PASSWORD}"]
    environment:
      REDIS_PASSWORD: ${BUZZ_REDIS_PASSWORD:?set BUZZ_REDIS_PASSWORD}
    volumes:
      - buzz-redis-data:/data
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$${REDIS_PASSWORD}\" ping | grep -q PONG"]
      interval: 5s
      timeout: 3s
      retries: 12
      start_period: 5s
    restart: unless-stopped
    networks:
      - buzz_net

  minio:
    image: minio/minio:RELEASE.2025-09-07T16-13-09Z
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${BUZZ_S3_ACCESS_KEY:?set BUZZ_S3_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${BUZZ_S3_SECRET_KEY:?set BUZZ_S3_SECRET_KEY}
    volumes:
      - buzz-minio-data:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:9000/minio/health/live"]
      interval: 5s
      timeout: 5s
      retries: 12
      start_period: 10s
    restart: unless-stopped
    networks:
      - buzz_net

  minio-init:
    image: minio/mc:RELEASE.2025-08-13T08-35-41Z
    depends_on:
      minio:
        condition: service_healthy
    environment:
      BUZZ_S3_ACCESS_KEY: ${BUZZ_S3_ACCESS_KEY:?set BUZZ_S3_ACCESS_KEY}
      BUZZ_S3_SECRET_KEY: ${BUZZ_S3_SECRET_KEY:?set BUZZ_S3_SECRET_KEY}
      BUZZ_S3_BUCKET: ${BUZZ_S3_BUCKET:-buzz-media}
    entrypoint: >
      /bin/sh -euc '
        mc alias set local http://minio:9000 "$${BUZZ_S3_ACCESS_KEY}" "$${BUZZ_S3_SECRET_KEY}"
        mc mb --ignore-existing "local/$${BUZZ_S3_BUCKET}"
        mc anonymous set none "local/$${BUZZ_S3_BUCKET}"
      '
    restart: "no"
    networks:
      - buzz_net

volumes:
  buzz-postgres-data:
    labels:
      com.buzz.volume: postgres
  buzz-redis-data:
    labels:
      com.buzz.volume: redis
  buzz-minio-data:
    labels:
      com.buzz.volume: minio
  buzz-git-data:
    labels:
      com.buzz.volume: git

networks:
  buzz_net:
    driver: bridge
    labels:
      com.buzz.network: production
```

- [ ] **Step 3: Diff against upstream to confirm only the intended deltas**

Run:
```bash
diff <(sed 's/#.*//' /tmp/buzz-upstream-compose.yml) <(sed 's/#.*//' deploy/docker-compose.buzz.yml) || true
```
Expected: differences limited to — `name:`, `buzz-net`→`buzz_net`, the `127.0.0.1:` port prefix, `env_file` removed + `environment:` expanded on `relay`, the `labels:` block on `relay`, the `BUZZ_` prefix on the four Postgres/Redis interpolation keys, `redis` gaining an `environment:` block, and the `BUZZ_IMAGE` default tag. No service added or removed. No healthcheck logic changed.

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.buzz.yml
git commit -m "feat(buzz): vendored compose for self-hosted Buzz relay

Vendored from block/buzz deploy/compose/compose.yml @ 7012d86. Own project
name 'buzz', relay on 127.0.0.1:3000 only, Traefik labels replace the bundled
Caddy, Postgres/Redis env keys prefixed BUZZ_ to avoid clashing with the
openbrain stack in the shared deploy/.env.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Document the env vars in `deploy/.env.example`

**Files:**
- Modify: `deploy/.env.example` (append at end)

- [ ] **Step 1: Append the buzz block**

Add these lines to the end of `deploy/.env.example`:

```bash

# --- buzz (docker-compose.buzz.yml) ---------------------------------------
# Relay image. Pin a release tag for production (not :main). Tags: https://github.com/block/buzz/tags
BUZZ_IMAGE=ghcr.io/block/buzz:v0.5.2

# Public host. Covered by the *.srv1608402.hstgr.cloud wildcard -- no DNS record needed.
BUZZ_DOMAIN=buzz.srv1608402.hstgr.cloud

# Owner identity: the 64-char HEX form of your Nostr npub (created in the Buzz
# desktop app). This account is the permanent relay owner/admin and cannot be
# removed. Convert npub -> hex with `nak decode <npub>` or https://nostrtool.com.
# The matching PRIVATE key (nsec) stays in your password manager -- never on the VPS.
RELAY_OWNER_PUBKEY=change-me-64-hex-owner-pubkey

# Relay's own signing identity. Generate ONCE, never rotate (changing it changes
# the relay's advertised pubkey):  openssl rand -hex 32
BUZZ_RELAY_PRIVATE_KEY=change-me-openssl-rand-hex-32

# Git-hook HMAC secret. Generate once:  openssl rand -hex 32
BUZZ_GIT_HOOK_HMAC_SECRET=change-me-openssl-rand-hex-32

# Postgres -- BUZZ_-prefixed so they don't collide with the openbrain stack's
# POSTGRES_* keys in this same file. Password is interpolated unescaped into a
# postgres:// URL -- use `openssl rand -hex 32` (no @ : / # % ? characters).
BUZZ_POSTGRES_DB=buzz
BUZZ_POSTGRES_USER=buzz
BUZZ_POSTGRES_PASSWORD=change-me-openssl-rand-hex-32

# Redis
BUZZ_REDIS_PASSWORD=change-me-openssl-rand-hex-32

# MinIO / S3 (also the MinIO root credentials). Generate each:  openssl rand -hex 32
BUZZ_S3_ACCESS_KEY=change-me-openssl-rand-hex-32
BUZZ_S3_SECRET_KEY=change-me-openssl-rand-hex-32
BUZZ_S3_BUCKET=buzz-media

# Closed-relay production defaults -- leave as-is unless you want an open relay.
BUZZ_REQUIRE_AUTH_TOKEN=true
BUZZ_REQUIRE_RELAY_MEMBERSHIP=true
BUZZ_ALLOW_NIP_OA_AUTH=true
BUZZ_AUTO_MIGRATE=true

# Relay loopback port on the VPS (debugging only; public access is via Traefik).
BUZZ_HTTP_PORT=3000
```

- [ ] **Step 2: Commit**

```bash
git add deploy/.env.example
git commit -m "docs(buzz): document BUZZ_* env vars in deploy/.env.example

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Validate the compose file locally

**Files:** none (validation only)

- [ ] **Step 1: Create a throwaway `.env` with dummy values**

```bash
cd deploy
cp .env.example .env.buzz-check
# fill the required (`:?`) vars with any non-empty junk so interpolation resolves
sed -i \
  -e 's/change-me-64-hex-owner-pubkey/0000000000000000000000000000000000000000000000000000000000000000/' \
  -e 's/change-me-openssl-rand-hex-32/deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef00/g' \
  .env.buzz-check
```

- [ ] **Step 2: Render the merged config**

Run:
```bash
docker compose --env-file .env.buzz-check -f docker-compose.buzz.yml config --quiet && echo "COMPOSE OK"
```
Expected: `COMPOSE OK`, exit 0, no `variable is not set` / `required` errors.

- [ ] **Step 3: Confirm the Traefik router + port render correctly**

Run:
```bash
docker compose --env-file .env.buzz-check -f docker-compose.buzz.yml config \
  | grep -E "traefik\.http\.routers\.buzz\.rule|127\.0\.0\.1:3000:3000|traefik\.docker\.network"
```
Expected, three lines showing:
- `traefik.http.routers.buzz.rule: Host(\`buzz.srv1608402.hstgr.cloud\`)`
- published port `127.0.0.1:3000:3000`
- `traefik.docker.network: buzz_buzz_net`

- [ ] **Step 4: Clean up**

```bash
rm deploy/.env.buzz-check
```

- [ ] **Step 5: Commit** — nothing to commit; note the check passed in the task log.

---

## Task 4: Write `BuzzDocu.md`

**Files:**
- Create: `BuzzDocu.md` (repo root)

- [ ] **Step 1: Write the file**

```markdown
# Buzz relay on srv1608402

**Status: PLANNED.** Flip to "DONE & LIVE" once Task 10 verification passes.

Self-hosted [Buzz](https://github.com/block/buzz) — Block's Nostr collaboration
platform (team chat, code repos, workflows, human + AI agents in shared rooms).

## What runs

| Piece | Detail |
| --- | --- |
| Compose file | `deploy/docker-compose.buzz.yml`, project **`buzz`** |
| Containers | `buzz-relay-1`, `buzz-postgres-1`, `buzz-redis-1`, `buzz-minio-1` (+ `buzz-minio-init-1` one-shot) |
| Public URL | `https://buzz.srv1608402.hstgr.cloud` — web UI + NIP-11; `wss://…` for the relay |
| Ingress | existing host-mode Traefik, router `buzz`, LE http-challenge cert. No Caddy. |
| Local port | `127.0.0.1:3000` on the VPS (debug only) |
| Network | `buzz_buzz_net` bridge, internal; nothing joins the Hermes network |
| Volumes | `buzz_buzz-postgres-data`, `buzz_buzz-redis-data`, `buzz_buzz-minio-data`, `buzz_buzz-git-data` |
| Mode | closed relay (`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`); owner = `RELAY_OWNER_PUBKEY` |

Vendored compose — upstream is `block/buzz` `deploy/compose/compose.yml` at
commit `7012d86`. See the header of `deploy/docker-compose.buzz.yml` for the
delta list. Upstream's `compose.caddy.yml` is deliberately not used.

## First deploy

See `docs/superpowers/plans/2026-09-08-buzz-relay-deploy.md` Tasks 7–10.
Summary:

1. Create the owner identity in the Buzz **desktop app**, back up its `nsec` to
   your password manager, convert the `npub` to 64-hex.
2. On the VPS: `cd /root/HermesPlusOpenbrain && git pull --ff-only`.
3. Add the `BUZZ_*` block to `deploy/.env` with generated secrets (see
   `deploy/.env.example`). `RELAY_OWNER_PUBKEY` = the hex from step 1.
4. `cd deploy && docker compose -f docker-compose.buzz.yml up -d --wait`.
5. Verify (Task 10): HTTPS 200 + valid cert, NIP-11 JSON, `wss://` handshake,
   owner recognised in the desktop app. Check no other service regressed.

## Members

Closed relay — people join only after the owner adds them:

```bash
cd /root/HermesPlusOpenbrain/deploy
docker compose -f docker-compose.buzz.yml exec relay \
  /usr/local/bin/buzz-admin add-member --pubkey <npub-or-hex> [--role member|admin]
docker compose -f docker-compose.buzz.yml exec relay /usr/local/bin/buzz-admin list-members
```
Add members one at a time with a `sleep 1` between — the roster is a single
kind:13534 event and same-second adds collide.

## Upgrade

```bash
cd /root/HermesPlusOpenbrain
git pull --ff-only                       # if the vendored file / pinned tag changed
$EDITOR deploy/.env                       # bump BUZZ_IMAGE to the new release tag
cd deploy
docker compose -f docker-compose.buzz.yml pull
docker compose -f docker-compose.buzz.yml up -d --wait
```
Run `scripts/buzz-backup.sh` first. `BUZZ_AUTO_MIGRATE=true` applies DB
migrations on start.

## Backup / restore

`scripts/buzz-backup.sh` (nightly cron, 7-day rotation in `/root/backups/buzz/`)
captures, from one window:

- `deploy/.env` `BUZZ_*` lines (also keep these in the password manager)
- `pg_dump` of the `buzz` database (gzipped)
- tar of the `buzz_buzz-minio-data` and `buzz_buzz-git-data` volumes

**Non-rotatable secrets** — losing them is unrecoverable: `RELAY_OWNER_PUBKEY`
(+ its nsec), `BUZZ_RELAY_PRIVATE_KEY`, `BUZZ_POSTGRES_PASSWORD`,
`BUZZ_S3_ACCESS_KEY` / `BUZZ_S3_SECRET_KEY`.

Restore: recreate `deploy/.env`, `docker compose … up -d postgres minio`, load
the `pg_dump` into `buzz`, untar the volumes, then start `relay`.

Off-box shipping of `/root/backups/buzz/` is a follow-up (same gap the rest of
the stack has today).

## Phase 2 (not built)

Moderation dashboard (`BUZZ_ADMIN_HOST=admin.buzz.srv1608402.hstgr.cloud`,
`BUZZ_ADMIN_AUTH=nip98`, behind Traefik) · off-box backups · Prometheus scrape
of `relay:9102`.
```

- [ ] **Step 2: Commit**

```bash
git add BuzzDocu.md
git commit -m "docs(buzz): add BuzzDocu.md operational reference

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Add Buzz to `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the services list**

Run:
```bash
grep -nE "openbrain|stripe|Traefik|hstgr\.cloud" README.md | head -30
```
Expected: locate the section/table that enumerates the deployed services (openbrain MCP, GUI, stripe MCP).

- [ ] **Step 2: Add a Buzz entry**

In the same format as the neighbouring entries, add a row/bullet:

> **Buzz relay** — `https://buzz.srv1608402.hstgr.cloud` (`wss://` for Nostr). Self-hosted Block Buzz collaboration relay. Compose: `deploy/docker-compose.buzz.yml`. Ops: [`BuzzDocu.md`](BuzzDocu.md).

Match the exact table columns / bullet style already used — do not restructure the section.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(buzz): list the Buzz relay in README

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Write the backup script

**Files:**
- Create: `scripts/buzz-backup.sh`

- [ ] **Step 1: Check whether `scripts/` exists and how other scripts look**

Run:
```bash
ls scripts/ 2>/dev/null || echo "no scripts dir yet"
```
If it does not exist, it will be created by the `git add` in Step 3.

- [ ] **Step 2: Write `scripts/buzz-backup.sh`**

```bash
#!/usr/bin/env bash
# Nightly Buzz backup. Install on the VPS:
#   chmod +x /root/HermesPlusOpenbrain/scripts/buzz-backup.sh
#   ( crontab -l 2>/dev/null; echo "17 3 * * * /root/HermesPlusOpenbrain/scripts/buzz-backup.sh >> /root/backups/buzz/cron.log 2>&1" ) | crontab -
set -euo pipefail

COMPOSE_DIR=/root/HermesPlusOpenbrain/deploy
COMPOSE_FILE=docker-compose.buzz.yml
DEST=/root/backups/buzz
KEEP_DAYS=7
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$DEST"
cd "$COMPOSE_DIR"

# 1. env (BUZZ_* lines only)
grep -E '^\s*(BUZZ_|RELAY_OWNER_PUBKEY)' .env > "$DEST/env-$STAMP.txt"

# 2. postgres logical dump
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$DEST/pg-$STAMP.sql.gz"

# 3. minio + git volumes (tar via a throwaway alpine mount)
for vol in buzz_buzz-minio-data buzz_buzz-git-data; do
  docker run --rm -v "$vol":/v -v "$DEST":/b alpine \
    tar czf "/b/${vol}-$STAMP.tar.gz" -C /v .
done

# 4. rotate
find "$DEST" -type f -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) buzz backup ok -> $DEST (pg-$STAMP.sql.gz)"
```

- [ ] **Step 3: Commit**

```bash
git add scripts/buzz-backup.sh
git commit -m "feat(buzz): nightly backup script (pg_dump + volume tars)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Push the branch and open a PR

**Files:** none

- [ ] **Step 1: Push**

```bash
git push -u origin feat/buzz-relay
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Self-hosted Buzz relay on srv1608402" --body "$(cat <<'EOF'
Deploys Block's Buzz (Nostr collaboration relay) alongside the openbrain/stripe
stacks.

- `deploy/docker-compose.buzz.yml` — vendored from block/buzz @ 7012d86, project
  `buzz`, Traefik labels replace the bundled Caddy, Postgres/Redis env keys
  prefixed `BUZZ_` to avoid clashing with openbrain in the shared `deploy/.env`.
- `deploy/.env.example` — documented `BUZZ_*` block.
- `BuzzDocu.md` — ops reference. `scripts/buzz-backup.sh` — nightly backup.
- Public at `https://buzz.srv1608402.hstgr.cloud` via the existing wildcard DNS.

Design: `docs/superpowers/specs/2026-09-08-buzz-relay-deploy-design.md`
Plan: `docs/superpowers/plans/2026-09-08-buzz-relay-deploy.md`

VPS deploy (Tasks 8–10) happens after review.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR URL printed. **Stop here for user review before touching the VPS.**

---

## Task 8: (USER ACTION) Create the Buzz owner identity

**This task is done by the user — no code.**

- [ ] **Step 1:** Install the Buzz desktop app (https://buzz.build or the `block/buzz` releases — `desktop-v*` tags). Browser-only access is not supported for setup.
- [ ] **Step 2:** Launch it, create a new Nostr identity.
- [ ] **Step 3:** Copy the **private key (`nsec1…`)** into your password manager. This is the only copy — it is never stored on the VPS.
- [ ] **Step 4:** Copy the **public key (`npub1…`)** and convert it to 64-char hex — `nak decode npub1…` (if you have [`nak`](https://github.com/fiatjaf/nak)) or paste into https://nostrtool.com and read the "hex" field.
- [ ] **Step 5:** Hand the **hex pubkey** to whoever runs Task 9 (it is not secret). Keep the app open for Task 11.

---

## Task 9: Provision `deploy/.env` on the VPS

**Files:**
- Modify (on VPS, uncommitted): `/root/HermesPlusOpenbrain/deploy/.env`

- [ ] **Step 1: Pull the merged branch**

```bash
ssh root@srv1608402.hstgr.cloud
cd /root/HermesPlusOpenbrain
git pull --ff-only
```
Expected: `deploy/docker-compose.buzz.yml` and the updated `.env.example` present.

- [ ] **Step 2: Generate secrets and append the buzz block to the real `.env`**

Run on the VPS (this writes generated values directly — nothing is echoed to chat):
```bash
cd /root/HermesPlusOpenbrain/deploy
cat >> .env <<EOF

# --- buzz (added $(date -Is)) ---
BUZZ_IMAGE=ghcr.io/block/buzz:v0.5.2
BUZZ_DOMAIN=buzz.srv1608402.hstgr.cloud
RELAY_OWNER_PUBKEY=REPLACE_WITH_HEX_FROM_TASK_8
BUZZ_RELAY_PRIVATE_KEY=$(openssl rand -hex 32)
BUZZ_GIT_HOOK_HMAC_SECRET=$(openssl rand -hex 32)
BUZZ_POSTGRES_DB=buzz
BUZZ_POSTGRES_USER=buzz
BUZZ_POSTGRES_PASSWORD=$(openssl rand -hex 32)
BUZZ_REDIS_PASSWORD=$(openssl rand -hex 32)
BUZZ_S3_ACCESS_KEY=$(openssl rand -hex 32)
BUZZ_S3_SECRET_KEY=$(openssl rand -hex 32)
BUZZ_S3_BUCKET=buzz-media
BUZZ_REQUIRE_AUTH_TOKEN=true
BUZZ_REQUIRE_RELAY_MEMBERSHIP=true
BUZZ_ALLOW_NIP_OA_AUTH=true
BUZZ_AUTO_MIGRATE=true
BUZZ_HTTP_PORT=3000
EOF
```

- [ ] **Step 3: Paste the owner hex from Task 8**

```bash
sed -i 's/REPLACE_WITH_HEX_FROM_TASK_8/<64-hex-owner-pubkey>/' .env
grep -E '^(RELAY_OWNER_PUBKEY|BUZZ_DOMAIN|BUZZ_IMAGE)=' .env
```
Expected: `RELAY_OWNER_PUBKEY` is 64 hex chars, `BUZZ_DOMAIN=buzz.srv1608402.hstgr.cloud`.

- [ ] **Step 4: Confirm no placeholders and no CHANGE_ME/openssl-literal slipped in**

Run:
```bash
grep -nE 'REPLACE_WITH|change-me|CHANGE_ME|rand -hex' .env || echo "no placeholders"
```
Expected: `no placeholders`.

- [ ] **Step 5: Back up the buzz env lines to the password manager** (user action) — copy the `BUZZ_*` + `RELAY_OWNER_PUBKEY` lines now, before first start.

---

## Task 10: Bring up the Buzz stack on the VPS

**Files:** none

- [ ] **Step 1: Pull images**

```bash
cd /root/HermesPlusOpenbrain/deploy
docker compose -f docker-compose.buzz.yml pull
```
Expected: `relay`, `postgres:17-alpine`, `redis:7-alpine`, `minio`, `minio/mc` pulled, no auth error (`ghcr.io/block/buzz` is public).

- [ ] **Step 2: Start, waiting for health**

```bash
docker compose -f docker-compose.buzz.yml up -d --wait
```
Expected: exits 0 after all services report healthy. If it times out, jump to Step 6.

- [ ] **Step 3: Check container state**

```bash
docker compose -f docker-compose.buzz.yml ps
```
Expected: `relay`, `postgres`, `redis`, `minio` = `Up (healthy)`; `minio-init` = `Exited (0)`.

- [ ] **Step 4: Confirm Traefik picked up the route** (cert issues on first request)

```bash
sleep 15
curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://buzz.srv1608402.hstgr.cloud/
```
Expected: `200 0` (`ssl_verify_result` 0 = valid chain). A `404`/`503` means Traefik isn't routing — check `docker logs traefik-traefik-1 --tail 50` for `buzz` and `traefik.docker.network`.

- [ ] **Step 5: Verify the relay identity document (NIP-11)**

```bash
curl -sS -H 'Accept: application/nostr+json' https://buzz.srv1608402.hstgr.cloud/ | python3 -m json.tool
```
Expected: JSON with `name`, `pubkey` (matches the pubkey derived from `BUZZ_RELAY_PRIVATE_KEY`), `supported_nips`, and the owner reflected. `RUST_LOG` in `docker compose -f docker-compose.buzz.yml logs relay` shows no panics.

- [ ] **Step 6: WebSocket handshake**

```bash
docker run --rm ghcr.io/vi/websocat:latest -q -1 \
  wss://buzz.srv1608402.hstgr.cloud/ <<<'["REQ","test",{"kinds":[1],"limit":1}]' || true
```
Expected: a JSON array response (`["EVENT",…]`, `["EOSE",…]`, or an auth challenge `["AUTH",…]` — any of these proves the WS upgrade works through Traefik). A hang/connection-refused means Traefik is not upgrading — check the router entrypoint is `websecure`.

- [ ] **Step 7 (only if Step 2 timed out): diagnose**

```bash
docker compose -f docker-compose.buzz.yml logs --tail 80 relay
docker compose -f docker-compose.buzz.yml logs --tail 40 postgres minio
```
Common causes: a `:?` var still empty (fix `.env`, re-run Step 2); `DATABASE_URL` rejected because `BUZZ_POSTGRES_PASSWORD` contains a URL-unsafe char (regen with `openssl rand -hex 32`); MinIO bucket race (re-run — `minio-init` is idempotent).

---

## Task 11: Regression check + connect the desktop app

**Files:** none (plus a docs flip in Step 4)

- [ ] **Step 1: Every pre-existing service still healthy**

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'traefik|hermes-agent|openbrain|stripe'
for h in brain gui; do curl -sS -o /dev/null -w "$h %{http_code}\n" https://$h.srv1608402.hstgr.cloud/ ; done
```
Expected: openbrain-db / openbrain-mcp / openbrain-gui / stripe-mcp / traefik / hermes-agent all `Up`, none `Restarting`; `brain` and `gui` return their normal codes (200 / 401).

- [ ] **Step 2: Host resources still comfortable**

```bash
free -h && df -h / && docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
```
Expected: several GiB RAM still available; `/` well under 80%.

- [ ] **Step 3: Connect from the Buzz desktop app** (user action)

Add relay `wss://buzz.srv1608402.hstgr.cloud` in the desktop app signed in as the Task 8 identity. Expected: connects; the account shows as **owner/admin**; you can create a room.

- [ ] **Step 4: Flip `BuzzDocu.md` to "DONE & LIVE"**

Edit the status line and the "First deploy" section of `BuzzDocu.md` to record what was actually done (dates, container names from `docker compose ps`, any deviation), matching how `stripe-mcp/DEPLOY.md` reads post-deploy.

```bash
git add BuzzDocu.md
git commit -m "docs(buzz): mark Buzz relay live on srv1608402

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push
```

---

## Task 12: Install the backup cron on the VPS

**Files:** none

- [ ] **Step 1: Enable the script**

```bash
ssh root@srv1608402.hstgr.cloud
chmod +x /root/HermesPlusOpenbrain/scripts/buzz-backup.sh
mkdir -p /root/backups/buzz
```

- [ ] **Step 2: Dry-run it**

```bash
/root/HermesPlusOpenbrain/scripts/buzz-backup.sh
ls -lh /root/backups/buzz/
```
Expected: `env-*.txt`, `pg-*.sql.gz` (non-empty), `buzz_buzz-minio-data-*.tar.gz`, `buzz_buzz-git-data-*.tar.gz`; final line `… buzz backup ok`.

- [ ] **Step 3: Schedule it**

```bash
( crontab -l 2>/dev/null; echo "17 3 * * * /root/HermesPlusOpenbrain/scripts/buzz-backup.sh >> /root/backups/buzz/cron.log 2>&1" ) | crontab -
crontab -l | grep buzz-backup
```
Expected: the cron line is listed.

---

## Task 13: Merge the PR

**Files:** none

- [ ] **Step 1: Confirm verification is green** — Tasks 10 and 11 all passed; `BuzzDocu.md` says "DONE & LIVE".

- [ ] **Step 2: Merge**

```bash
gh pr merge feat/buzz-relay --squash --delete-branch
```

- [ ] **Step 3: Sync the VPS to merged `main`**

```bash
ssh root@srv1608402.hstgr.cloud 'cd /root/HermesPlusOpenbrain && git checkout main && git pull --ff-only'
```
Expected: fast-forwards to the squash commit; `docker compose -f deploy/docker-compose.buzz.yml ps` still healthy (compose file content unchanged by the merge).

- [ ] **Step 4: Update memory**

Add a `project` memory `project-buzz-relay.md` (and an index line in `MEMORY.md`): Buzz relay live on srv1608402 as of <date>, compose `deploy/docker-compose.buzz.yml` project `buzz`, public `wss://buzz.srv1608402.hstgr.cloud`, closed relay, ops ref `BuzzDocu.md`, phase 2 = moderation dashboard + off-box backups. Link `[[project-stripe-mcp]]`, `[[project-openbrain-webgui]]`.

---

## Self-review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| 4.1 approach (git-managed, not one-click / not new VPS) | Task 1 |
| 4.2 `deploy/docker-compose.buzz.yml` deltas | Task 1 |
| 4.2 `deploy/.env.example` block | Task 2 |
| 4.2 `BuzzDocu.md` | Tasks 4, 11 |
| 4.2 `README.md` row | Task 5 |
| 4.3 Traefik labels + `traefik.docker.network` | Task 1 step 2, Task 10 step 4 |
| 4.4 closed-relay config | Task 1 step 2, Task 2 |
| 4.5 backups | Tasks 6, 12 |
| 4.6 firewall (no change) | n/a — asserted in spec, nothing to do |
| owner identity creation | Task 8 |
| §7 verification (ps / HTTPS / NIP-11 / WS / regression) | Tasks 10, 11 |
| phase-2 items stay out | not in any task, by design |

**Placeholder scan:** `REPLACE_WITH_HEX_FROM_TASK_8` and `<64-hex-owner-pubkey>` in Task 9 are deliberate — the owner key comes from a user action in Task 8 and must not be invented. Task 9 step 4 explicitly greps to prove they were replaced. `BuzzDocu.md` ships with `Status: PLANNED` and is flipped in Task 11 step 4 — intentional, mirrors `stripe-mcp/DEPLOY.md`. Task 5 leaves the exact README wording to match surrounding style rather than guessing the table shape — the bullet content is fully specified.

**Type/name consistency:** compose project `buzz` → network `buzz_buzz_net` → volumes `buzz_buzz-*` used consistently in Task 1, `BuzzDocu.md`, `scripts/buzz-backup.sh`, Task 10/11. Env keys `BUZZ_POSTGRES_PASSWORD` / `BUZZ_REDIS_PASSWORD` / `BUZZ_S3_ACCESS_KEY` / `BUZZ_S3_SECRET_KEY` / `RELAY_OWNER_PUBKEY` / `BUZZ_RELAY_PRIVATE_KEY` identical across Tasks 1, 2, 9 and the backup script. Router/service name `buzz` consistent in the labels and the Task 10 curl checks.

---

## Execution handoff

Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks. Note Tasks 8 and 11-step-3 are user actions and Task 7 is a hard stop for PR review before any VPS change.

**2. Inline Execution** — run tasks in this session with checkpoints at Task 7 (PR review) and after Task 10 (deploy verification).

Which approach?
