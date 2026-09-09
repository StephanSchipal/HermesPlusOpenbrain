# Buzz relay on srv1608402

**Status: DONE & LIVE since 2026-09-09.** Deployed to `srv1608402`, healthy on
first boot, verified (HTTPS + LE cert, NIP-11, `wss://` 101 upgrade through
Traefik, owner in the membership roster, no regression to the existing stack).
This file is now the redeploy / upgrade / backup runbook.

Self-hosted [Buzz](https://github.com/block/buzz) — Block's Nostr collaboration
platform (team chat, code repos, workflows, human + AI agents in shared rooms).

- Relay image: `ghcr.io/block/buzz:sha-3c7f288` (no semver image tags upstream — pin `sha-<7>`).
- Relay's advertised identity (`self` in NIP-11): `9ee115fce4243dff3e2c280144fff19e5286daebac9dedf9680bf5d7c7e5168b`.
- Owner (`RELAY_OWNER_PUBKEY`): `f978cb69…56aa6` — Stephan's Buzz desktop identity; nsec in the password manager, keypair is device-bound to that machine.
- `deploy/.env` backup before the buzz block was added: `deploy/.env.pre-buzz-20260909` on the VPS.

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

## Redeploy from scratch

If the stack is ever wiped (volumes intact = data survives):

1. Owner identity: the Buzz **desktop app** shows the pubkey as raw hex under
   Settings → Identity (no `npub` conversion needed). `RELAY_OWNER_PUBKEY` = that hex.
   The private key is device-bound — back up the `nsec` (Settings → Private key →
   Reveal) to a password manager or the owner role is unrecoverable.
2. VPS host shell (NOT the hermes-agent container — use hPanel → Browser terminal
   if your SSH key lands you in `/opt/hermes`):
   `cd /root/HermesPlusOpenbrain && git pull --ff-only`.
3. `deploy/.env` needs the `BUZZ_*` block (see `deploy/.env.example`). Secrets:
   `openssl rand -hex 32` each. `BUZZ_IMAGE` = a `sha-<7>` tag from
   `github.com/block/buzz` commits.
4. `cd deploy && docker compose -f docker-compose.buzz.yml up -d --wait`.
5. Verify: `curl -sI https://buzz.srv1608402.hstgr.cloud/` (200 + cert);
   `curl -H 'Accept: application/nostr+json' https://buzz.srv1608402.hstgr.cloud/`
   (NIP-11 JSON); `curl -o /dev/null -w '%{http_code}' --http1.1 -H 'Connection: Upgrade'
   -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13'
   -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://buzz.srv1608402.hstgr.cloud/`
   (expect 101). Then add the relay in the desktop app and confirm owner/admin.

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
