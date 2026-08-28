# The `openbrain` WhatsApp bot

**Status: Live since 2026-08-28.** WhatsApp runs on a dedicated Hermes profile
(`openbrain`), a clone of the main profile with `laptop_fs` removed. Voice, CLI,
and laptop/desktop recall stay on the main profile (`default`).

Like [`TwilioDocu.md`](TwilioDocu.md) and [`Tailscale.md`](Tailscale.md), this
documents VPS-side infrastructure on the Hermes-Agent host — nothing here is
deployed or owned by this repo's `deploy/`.

- **VPS:** `srv1608402.hstgr.cloud`, container `hermes-agent-7qpk-hermes-agent-1`
  (`ghcr.io/hostinger/hvps-hermes-agent:latest`, Hermes v0.20.5, s6-overlay).
- **`$HERMES_HOME`:** `/opt/data` (the persistent bind mount
  `/docker/hermes-agent-7qpk/data`).

## Why

Every WhatsApp turn on the single `default` profile paid for the full system
prompt — every skill, the `openbrain` **and** `laptop_fs` MCP tool schemas — plus
(historically the real cost driver, see `README.md` "Hermes cost tuning") a
WhatsApp session that had grown for weeks. Moving WhatsApp to its own profile
gives a fresh session and lets the bot be tuned down independently (drop MCPs,
prune skills, swap model) without touching `default`.

The saving is **moderate, not dramatic**: `laptop_fs`'s ~dozen tool schemas out
of every turn, a fresh session, and headroom for later trimming. It is not a
lean capture-only bot — it is a full clone that also does general chat.

## The hard constraint

Hermes' WhatsApp channel is a Baileys "linked device" bridge, and Hermes blocks
two profiles from serving the same WhatsApp account. So the number could not be
split (capture on one profile, chat on another) — it **moved wholesale** from
`default` to `openbrain`, which required unlinking the old device and scanning a
new QR.

## Profile layout

| Profile | Path | Owns | api_server port |
|---|---|---|---|
| `default` | `/opt/data` | Twilio voice, CLI, `laptop_fs` MCP, `openbrain` MCP (laptop/desktop/CLI recall), all skills, `voice-server-watchdog` cron. **No WhatsApp.** | 8642 |
| `master` | `/opt/data/profiles/master` | Future orchestrator/coordinator for specialist bots (none defined yet). Idle clone. | 8643 |
| `openbrain` | `/opt/data/profiles/openbrain` | **WhatsApp** (capture + recall + general chat), `openbrain` MCP only, all cloned skills, `weekly-whatsapp-session-reset-reminder` cron. | 8644 |

All three: `anthropic/claude-sonnet-5`, `cache_ttl: 1h`, `compression.threshold: 0.2`.

## How multi-profile gateways are supervised (Hostinger image internals)

The image is built for this. On every container boot,
`/etc/cont-init.d/02-reconcile-profiles` runs `python -m hermes_cli.container_boot`,
which:

1. walks `$HERMES_HOME/profiles/*` (plus the root `default`),
2. recreates each profile's s6 service slot at `/run/service/gateway-<name>`
   (tmpfs — wiped every restart),
3. auto-starts the gateway **iff** the profile's
   `gateway_state.json` `desired_state` is `running` — separate-mode only
   (`GATEWAY_MULTIPLEX_PROFILES` env unset; if it were set, only the default
   gateway would run and it would multiplex all profiles).

So `openbrain`'s gateway survives `docker compose up --force-recreate` and
Hermes image updates with **no watchdog cron** — verified 2026-08-28 across two
recreates. `gateway_state.json` and the profile dir live on the persistent
volume; only the s6 slot is ephemeral and regenerated.

### Operational gotchas

- **`hermes -p <profile> gateway start` is a no-op on this image** (it targets a
  systemd/launchd service that doesn't exist here). To act on a gateway live:
  - up: `docker exec <C> bash -lc 'rm -f /run/service/gateway-<name>/down; /command/s6-svc -u /run/service/gateway-<name>'`
  - restart: `/command/s6-svc -r /run/service/gateway-<name>`
  - down: `/command/s6-svc -d /run/service/gateway-<name>`
- **Each profile's gateway binds its own `api_server` port.** A cloned profile
  inherits nothing here and defaults to 8642, colliding with `default` →
  `startup_failed: api_server_port_in_use`. Fix:
  `hermes -p <name> config set platforms.api_server.extra.port <free port> --force`.
- **WhatsApp on/off is the `.env` var `WHATSAPP_ENABLED`** (`true`/`false`) in
  the profile's `.env`. `gateway.whatsapp.enabled` is **not** a real config key
  (Hermes warns and ignores it).
- **The Twilio voice server does not auto-start after a container recreate.**
  Kick it: `docker exec <C> bash /opt/data/scripts/voice_watchdog.sh` (the
  `voice-server-watchdog` cron does it within 5 min otherwise). Also re-check the
  container bridge IP against `/docker/traefik/dynamic/voice.yml` — it was
  `172.16.1.2` and unchanged, but verify.

## What was done (2026-08-28)

1. `hermes profile create openbrain --clone-from default` — full clone (config,
   `.env`, `SOUL.md`, 129 skills). Fresh session + memory.
2. `hermes -p openbrain mcp remove laptop_fs` — the only capability change.
3. `hermes -p openbrain config set platforms.api_server.extra.port 8644 --force`.
4. Pinned the voice server to `default`: in `/opt/data/hermes_voice/agent.py`,
   both `hermes chat` invocations now start `config.HERMES_BIN, "-p", "default", "chat", …`.
   **The sticky default must stay `default`** — never run `hermes profile use openbrain`.
5. WhatsApp cutover:
   - `default` `.env`: `WHATSAPP_ENABLED=true → false`; moved
     `/opt/data/whatsapp/session` aside; restarted `gateway-default` (bridge stops).
   - `openbrain` `.env`: `WHATSAPP_ENABLED=false → true` (mode/allowed-users/
     home-channel were cloned correctly: `WHATSAPP_MODE=self-chat`).
   - Unlinked the old device on the phone; `hermes -p openbrain whatsapp` (needs
     a real TTY — run it via `ssh -t … docker exec -it …`), scanned the QR.
   - Restarted `gateway-openbrain` → `[Whatsapp] Bridge ready (status: connected)`.
   - `hermes -p openbrain pairing approve whatsapp <code>` — a fresh profile
     doesn't inherit `default`'s user authorization, so the first WhatsApp
     message returns a pairing code that must be approved once.
6. Moved cron `weekly-whatsapp-session-reset-reminder` (`0 9 * * 1`, `--no-agent`,
   `--deliver whatsapp:Stephan`, script `weekly-session-reset-reminder.sh`) from
   `default` to `openbrain`. `voice-server-watchdog` stayed on `default`.
7. Cleared `master`'s stale `whatsapp: fatal/not_paired` entry (leftover from an
   earlier clone) by stripping the key from its `gateway_state.json`.

Rollback image tag kept: `hvps-hermes-agent:pre-openbrain-bot-2026-08-28`.

## Verified working (2026-08-28, incl. after a container recreate)

```
$ hermes gateway list
  ✓ default (current)   ✓ master   ✓ openbrain

$ hermes -p openbrain mcp list
  openbrain  ✓ enabled
  mcpmarket_findflights_via_duffel  ✗ disabled     # no laptop_fs
```

- WhatsApp: real link → German summary + keywords + saved; recall in different
  wording; general chat — all work.
- Voice call to +43 1 4351876 — works (on `default`).
- `hermes -p default chat -q "ob stats"` — works (`default` keeps `openbrain` MCP).
- `openbrain` gateway + WhatsApp pairing + user approval all survived
  `docker compose up --force-recreate`.

## Reversing it

1. On the phone: unlink the `openbrain` WhatsApp device.
2. `openbrain` `.env`: `WHATSAPP_ENABLED=false`; `default` `.env`:
   `WHATSAPP_ENABLED=true`.
3. `hermes -p default whatsapp` (TTY) → re-pair `default`.
4. `/command/s6-svc -r` both gateways.
5. Move the cron back; `hermes profile delete openbrain`.

Captures are never at risk — they live in `openbrain-db`, untouched throughout.

## Re-pairing if the WhatsApp session breaks

`ssh root@srv1608402.hstgr.cloud -t 'docker exec -it hermes-agent-7qpk-hermes-agent-1 hermes -p openbrain whatsapp'`,
scan the QR, then `/command/s6-svc -r /run/service/gateway-openbrain`.

## Known follow-ups (not done here)

- **Prune skills** from `openbrain` — per-bot, via Hermes Desktop (each bot
  self-manages). The clone carries all 129.
- **Swap the model** on `openbrain` to something cheaper — user's call, one
  `hermes -p openbrain config set model.default …`.
- **Profile-aware cost visibility.** The GUI Cost page and `hermes insights`
  read one profile's `state.db`; WhatsApp usage now lives in
  `/opt/data/profiles/openbrain/state.db`. Interim: `openbrain insights`. The
  rework is code-only — named-profile `state.db` files are already inside the
  Cost page's existing `/hermes-data` bind mount
  (`/hermes-data/profiles/*/state.db`).
- **The `master` orchestrator** and other specialist bots (Designer, Writer,
  Builder, Researcher) — a separate effort.

Design + plan: [`docs/superpowers/specs/2026-08-27-openbrain-whatsapp-capture-bot-design.md`](docs/superpowers/specs/2026-08-27-openbrain-whatsapp-capture-bot-design.md)
· [`docs/superpowers/plans/2026-08-28-openbrain-whatsapp-bot.md`](docs/superpowers/plans/2026-08-28-openbrain-whatsapp-bot.md).
