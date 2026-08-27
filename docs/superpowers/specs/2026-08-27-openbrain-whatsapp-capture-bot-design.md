# `openbrain` bot — a cloned Hermes profile that owns the WhatsApp channel — Design

**Date:** 2026-08-27
**Status:** Draft — awaiting user sign-off

## Goal

Move the WhatsApp channel off the main Hermes-Agent profile onto a dedicated
profile (`openbrain`) that is a **clone** of it, so that:

- WhatsApp keeps working exactly as today — link → German summary + ~5 keywords
  + `mcp_openbrain_save`, recall ("ob suchen …"), *and* general chat;
- the main profile keeps everything else (Twilio voice, CLI, `laptop_fs`,
  `openbrain` MCP for laptop/desktop recall, all skills);
- the `openbrain` bot can then be tuned down over time (prune skills, swap model)
  independently, via Hermes Desktop, without touching the main profile.

This is the first step of a larger direction (a Master orchestrator bot and
specialist bots — Designer, Writer, Builder, Researcher — later), but those are
**out of scope here** (see Non-goals).

## Terminology

- **`default`** — the existing Hermes-Agent profile (`$HERMES_HOME` =
  `/opt/data` on the VPS). Owns WhatsApp today, plus Twilio voice, CLI,
  `laptop_fs` MCP, `openbrain` MCP, all skills.
- **`openbrain`** — the new profile this spec creates.
- **Master bot** — a future orchestrator profile. Not defined, not built here.

## Key constraint

Hermes' WhatsApp channel is a Baileys "linked device" session, and **each
profile runs its own gateway with its own WhatsApp link**. Hermes blocks two
profiles from serving the same WhatsApp account. So the number cannot be shared —
it moves wholesale from `default` to `openbrain`. Since the `openbrain` bot is a
full clone (keeps general chat and all skills), nothing is lost on WhatsApp by
the move.

Twilio voice is unaffected — it is a separate FastAPI process that shells out to
the Hermes CLI, not a gateway channel.

## Design

### 1. Create `openbrain` as a clone of `default`

```
hermes profile create openbrain --clone-from default
```

`--clone` / `--clone-from` copies `config.yaml`, `.env`, `SOUL.md`, and skills,
with fresh sessions and memory. The fresh session is a feature here — `default`'s
live WhatsApp session had accumulated for weeks (the real driver behind the
2026-08-14 cost spike, per `README.md`); `openbrain` starts clean.

### 2. The only change to the clone (for now): drop `laptop_fs`

Remove the `laptop_fs` MCP server from `openbrain`'s `mcp_servers`
(via `hermes mcp` tooling on that profile, or the Desktop Capabilities tab).
That removes ~a dozen filesystem tool schemas from every WhatsApp turn's
always-on prompt. `laptop_fs` is only ever used from the laptop itself, never
from WhatsApp.

**Everything else stays as cloned** — all skills, the `openbrain` MCP server,
`web`/`terminal`/`read_file` toolsets, model (`anthropic/claude-sonnet-5`),
`cache_ttl: 1h`, `compression.threshold: 0.2`. Pruning skills and swapping the
model are deliberately deferred to the user, per-bot, via Hermes Desktop.

### 3. Move the WhatsApp number

1. On `openbrain`: `openbrain gateway setup` → WhatsApp → prints a QR.
2. On the phone: WhatsApp → Linked Devices → unlink the existing Hermes device →
   scan the new QR.
3. Stop / disable `default`'s WhatsApp channel so it does not reconnect and trip
   the same-account block. (Open question below: stop `default`'s gateway
   entirely vs. just remove WhatsApp from its config — depends on whether
   WhatsApp is `default`'s only messaging platform, which it currently is.)

### 4. Pin Twilio voice to `default`

The voice server (`/opt/data/hermes_voice/`) shells out to `hermes`. Pin that
invocation to `-p default` (or set `HERMES_HOME=/opt/data` explicitly in
`voice_server.py` / `agent.py`) so that if `openbrain` is ever made the sticky
default, voice calls are not silently redirected to it.

### 5. Keep `openbrain`'s gateway running

The Hostinger-managed image autostarts one gateway. Options, to be resolved by
the spike (§ Risks):

- **Best case:** the image's gateway autostart can be pointed at `openbrain`
  (e.g. `hermes profile use openbrain` making it sticky-default, *if* that
  doesn't break voice — hence step 4 first). Then no watchdog is needed.
- **Fallback:** a `no_agent` cron job on `default` (whose scheduler is reliably
  up after boot) runs a shell script under `/opt/data/scripts/` that checks the
  `openbrain` gateway and restarts it — mirroring the existing
  `voice-server-watchdog` pattern (`project-hermes-agent-voice-update`).

Everything `openbrain` depends on lives under `/opt/data/profiles/openbrain/`,
which is inside the persistent bind mount.

### 6. `default` after the move

Unchanged except it no longer serves WhatsApp: Twilio voice, CLI, `laptop_fs`,
**`openbrain` MCP (kept — laptop/desktop/CLI recall still works)**, all skills,
the `voice-server-watchdog` cron. If it had a WhatsApp-reset reminder cron, that
moves to `openbrain`.

### Recall works from both profiles

Both keep the `openbrain` MCP server pointed at the same `openbrain-mcp` →
`openbrain-db`. `openbrain-mcp` already serves many concurrent clients (GUI,
Claude Desktop, Claude Code, Hermes). "ob suchen …" works on WhatsApp (via
`openbrain`) and from the laptop/CLI (via `default`). No coordination needed.

## Non-goals

- Pruning skills from the `openbrain` clone — the user does this later, per-bot,
  via Hermes Desktop.
- Changing the `openbrain` bot's model — stays Sonnet 5 as cloned; swappable
  later by the user.
- The Master orchestrator bot and the other specialist bots.
- Reworking the GUI Cost page / `hermes insights` for multiple profiles — a
  separate project, to be done once the other bots exist. (Noted as an impact
  below.)
- Any change to `openbrain-mcp`, `openbrain-db`, or the GUI code.
- A second WhatsApp number, or a non-WhatsApp channel for capture.

## Risks & things to verify

1. **Hostinger image behaviour with a non-`default` profile (highest risk).**
   The image regenerated `mcp_servers` from "another source of truth on boot" in
   Phase 5 (`README.md`) and autostarts a gateway. Unknown whether it tolerates
   `openbrain` owning the gateway across a `docker compose up --force-recreate`.
   **Spike first:** create a throwaway named profile, start its gateway, recreate
   the container, see what survives — before the real cutover. Register MCP via
   `hermes mcp` tooling, never a hand-edited `config.yaml` (that didn't survive a
   restart in Phase 5).
2. **Which gateway does the image autostart, and can it be repointed?** Drives
   the §5 decision (repoint vs. watchdog cron).
3. **8 GB VPS** — one gateway is replaced by another (net zero new always-on
   process if `default`'s gateway is stopped). Confirm `default`'s now-idle
   gateway isn't error-looping.
4. **WhatsApp re-pair interruption** — brief downtime on the number while the old
   device is unlinked and the new QR scanned. Do it at a quiet time.

## Impacts (accepted)

- **Cost visibility will fragment** once WhatsApp usage lands in
  `/opt/data/profiles/openbrain/state.db` — the GUI Cost page and
  `hermes insights` read one profile's `state.db`. Interim: `openbrain insights`
  for the bot's numbers. The profile-aware Cost page rework is a separate,
  later project (bundled with the other-bots work). Note: named-profile
  `state.db` files are **already inside** the Cost page's existing
  `/hermes-data` bind mount (`/hermes-data/profiles/*/state.db`), so that rework
  is pure code, no compose change.
- **Skill edits** to `openbrain-capture` / `whatsapp-kommunikation` now need to
  be made on the `openbrain` copy (the one serving WhatsApp). The `default`
  copies become dormant.

## Reversibility

Unlink `openbrain`'s WhatsApp device → re-pair to `default` → re-enable
`default`'s WhatsApp channel → `hermes profile delete openbrain` → remove the
watchdog cron if one was added. No data loss — captures live in `openbrain-db`,
untouched throughout.

## Deliverables

1. The `openbrain` profile on the VPS: cloned, `laptop_fs` removed, WhatsApp
   re-paired, gateway kept alive, Twilio pinned to `default`.
2. A doc in this repo describing the running two-profile setup — a new
   `*.md` (matching how `TwilioDocu.md` / `Tailscale.md` document VPS-side
   infrastructure), plus a short "Related:" pointer in `README.md`. No code
   changes to this repo.

## Open questions

- **Stop `default`'s gateway entirely, or just remove WhatsApp from its config?**
  Resolve during the spike, based on how the image treats a platform-less
  `default`.
- **Repoint the image's gateway autostart to `openbrain`, or add a watchdog
  cron?** Resolve during the spike.
