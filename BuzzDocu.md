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
