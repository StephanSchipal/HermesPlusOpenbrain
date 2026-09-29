# The `openbrain` WhatsApp bot

**Status: Live since 2026-08-28.** WhatsApp runs on a dedicated Hermes profile
(`openbrain`), a clone of the main profile with `laptop_fs` removed. Voice, CLI,
and laptop/desktop recall stay on the main profile (`default`).

Like [`TwilioDocu.md`](TwilioDocu.md) and [`Tailscale.md`](Tailscale.md), this
documents VPS-side infrastructure on the Hermes-Agent host — nothing here is
deployed or owned by this repo's `deploy/`.

- **VPS:** `srv1608402.hstgr.cloud`, container `hermes-agent-7qpk-hermes-agent-1`
  (`ghcr.io/hostinger/hvps-hermes-agent:latest`, Hermes v0.21.5 since
  2026-09-29, s6-overlay).
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
| `default` | `/opt/data` | Twilio voice, CLI, `laptop_fs` MCP, `openbrain` MCP (laptop/desktop/CLI recall), all skills, `voice-server-watchdog` cron. **No WhatsApp.** Its gateway is the host **multiplexer** (see below). | 8642 |
| `master` | `/opt/data/profiles/master` | Future orchestrator/coordinator for specialist bots (none defined yet). Idle clone. Served by the multiplexer. | `8642/p/master/v1` |
| `openbrain` | `/opt/data/profiles/openbrain` | **WhatsApp** (capture + recall + general chat), `openbrain` MCP only, all cloned skills, `weekly-whatsapp-session-reset-reminder` cron. **Standalone** gateway. | 8644 |

All three: `anthropic/claude-sonnet-5`, `cache_ttl: 1h`, `compression.threshold: 0.2`.
The specialist profiles added later (`coder`, `designer`, `researcher`, `writer`)
are served by the multiplexer like `master`.

## How multi-profile gateways are supervised (since v0.21, 2026-09-29)

Hermes v0.21 switched containers to **one gateway per host**. On every container
boot, `/etc/cont-init.d/02-reconcile-profiles` (`python -m hermes_cli.container_boot`):

1. walks `$HERMES_HOME/profiles/*` (plus the root `default`),
2. recreates each profile's s6 service slot at `/run/service/gateway-<name>`
   (tmpfs, so it is wiped on every restart),
3. starts **only** the root slot `gateway-default`. It "inherits" the run
   intent of every named profile and **multiplexes** them all. Named slots are
   registered but always left **down**. `gateway.multiplex_profiles: false` is
   retired and no longer works as an opt-out.

Under the multiplexer the named profiles' `api_server`s move to
`http://127.0.0.1:8642/p/<profile>/v1`. Nothing of ours used their old ports.

### Why `openbrain` is standalone

The multiplexer serves WhatsApp **only on the `default` profile**. Its log line is:
`whatsapp is enabled in profile(s) openbrain but not on the default profile — the
platform is not being served`. Upstream lists "WhatsApp bridge/relay on
secondaries" as an open multiplexing gap. So `openbrain` opts out:

```yaml
# /opt/data/profiles/openbrain/config.yaml
gateway:
  standalone: true
```

The multiplexer then skips `openbrain`, and `openbrain` runs its own
`hermes -p openbrain gateway run` (own WhatsApp bridge, own cron, `api_server`
on 8644). `hermes config set` warns that `gateway.standalone` is "not a
recognized config key". Ignore that, because the gateway does read it.

**Nothing in the image starts a standalone slot on boot.**
`hermes -p openbrain gateway start` does not survive a container restart either
(verified). So a **host** cron job brings the slot up:

```
* * * * * /root/HermesPlusOpenbrain/scripts/hermes-standalone-gateways-watchdog.sh
```

It only acts on profiles that really have `standalone: true`, so it never starts
a second gateway for a multiplexed profile. It logs to
`/var/log/hermes-standalone-gateways-watchdog.log`. After a recreate or restart,
`openbrain` is back within about 1 minute (a line `openbrain: gateway slot was
down — started`).

`gateway.standalone` is marked upstream as a **temporary compatibility shim**.
On every future Hermes update, check whether WhatsApp on secondary profiles
works under the multiplexer. If it does, fold `openbrain` back with
`hermes gateway migrate --multiplex`, remove `standalone`, and drop the cron line.
Full background: [`docs/hermes-update-v0.21.5.md`](docs/hermes-update-v0.21.5.md).

### Operational gotchas

- **To act on a gateway live, use s6 directly.** `hermes -p <profile> gateway start`
  does not persist across restarts here.
  - up: `docker exec <C> bash -lc 'rm -f /run/service/gateway-<name>/down; /command/s6-svc -u /run/service/gateway-<name>'`
  - restart: `/command/s6-svc -r /run/service/gateway-<name>`
  - down: `/command/s6-svc -d /run/service/gateway-<name>` (for `openbrain`, the
    watchdog brings it back up within a minute. Comment out its cron line first
    if you need it to stay down.)
  - The `<name>` is `default` (the multiplexer, so this affects every profile
    except `openbrain`) or `openbrain`. **Never** start another named slot by
    hand: a second gateway would fight the multiplexer over that profile.
- **Only standalone gateways bind their own `api_server` port.** If another
  profile is ever made standalone, give it a free port or it collides with 8642
  (`startup_failed: api_server_port_in_use`):
  `hermes -p <name> config set platforms.api_server.extra.port <free port> --force`.
- **WhatsApp on/off:** the `.env` var `WHATSAPP_ENABLED` (`true`/`false`) in the
  profile's `.env`, **but** since v0.21 an explicit disable in `config.yaml` wins
  over the env var. `openbrain`'s config had a leftover
  `gateway.whatsapp.enabled: false`, which v0.20.x ignored and v0.21 treats as
  "off". It was removed on 2026-09-29. `master`'s config still carries the same
  leftover, which is harmless because its WhatsApp is off anyway. If WhatsApp
  silently stays down, grep the profile's `config.yaml` for `whatsapp:`.
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

**Re-verified 2026-09-29 after the v0.21.5 update** (multiplexer + standalone
`openbrain`): WhatsApp save, Buzz mentions (`@Hermes`, `@Hermes-openbrain`), a
Twilio test call, and the GUI Cost page were all confirmed working by Stephan.
On the server side, the WhatsApp bridge reported `connected`, all 7 profiles
were `running`, and the MCPs `openbrain`, `stripe` and `laptop_fs` were
connected.

## Reversing it

1. On the phone: unlink the `openbrain` WhatsApp device.
2. `openbrain` `.env`: `WHATSAPP_ENABLED=false`; `default` `.env`:
   `WHATSAPP_ENABLED=true`.
3. `hermes -p default whatsapp` (TTY) → re-pair `default`.
4. Remove `gateway.standalone` from `openbrain`'s config, then remove the
   `hermes-standalone-gateways-watchdog.sh` line from the host crontab.
   `/command/s6-svc -d` `gateway-openbrain`, then `-r` `gateway-default`.
   From then on the multiplexer serves `openbrain` too.
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
- ~~**Profile-aware cost visibility.**~~ Done 2026-08-29: the GUI Cost page has
  a per-bot profile dropdown plus an "All" view (see `costpage.md`,
  "Per-bot view").
- **The `master` orchestrator** and other specialist bots (Designer, Writer,
  Builder, Researcher) — a separate effort.

Design + plan: [`docs/superpowers/specs/2026-08-27-openbrain-whatsapp-capture-bot-design.md`](docs/superpowers/specs/2026-08-27-openbrain-whatsapp-capture-bot-design.md)
· [`docs/superpowers/plans/2026-08-28-openbrain-whatsapp-bot.md`](docs/superpowers/plans/2026-08-28-openbrain-whatsapp-bot.md).
