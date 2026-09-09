# Hermes agents in Buzz

**Status: build-out in progress — spike passed 2026-09-09.**
(Flip to `**v1 LIVE 2026-09-__**` once the fleet is verified.)

Bridges Hermes profiles into the live Buzz relay ([`buzz.md`](buzz.md)) as
member identities, via `buzz-acp` → `hermes -p <profile> acp`.

- Design: [`docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md`](docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md)
- Spike findings: [`docs/superpowers/notes/2026-09-09-hermes-buzz-agents-spike.md`](docs/superpowers/notes/2026-09-09-hermes-buzz-agents-spike.md)
- Plan: [`docs/superpowers/plans/2026-09-09-hermes-buzz-agents.md`](docs/superpowers/plans/2026-09-09-hermes-buzz-agents.md)

## What runs

- `/opt/data/bin/buzz-acp` + `/opt/data/bin/buzz.real` — built from
  `block/buzz@3c7f288` (musl-static). `/opt/data/bin/buzz-acp.version` records
  the commit, build date, and sha256.
- `/opt/data/buzz-agents/<profile>.env` (chmod 600) — `buzz-acp` config,
  including `BUZZ_PRIVATE_KEY` (its own relay auth).
- `/opt/data/buzz-agents/<profile>.key` (chmod 600) — the bare hex key; the
  reply wrapper reads this.
- `/opt/data/buzz-agents/enabled` — newline list of profile names to run. **The
  "launch N of 7" knob.** Blank lines and `#`-prefixed lines are ignored.
- `/opt/data/buzz-agents/supervise.sh` — copy of
  `scripts/buzz-agent-supervise.sh`. One `buzz-acp` per enabled name; also
  reinstalls the wrapper.
- `/opt/data/buzz-agents/buzz-wrap.sh` → `/usr/local/bin/buzz` — the reply
  wrapper. `/usr/local/bin` is **not** a Docker volume; the supervisor
  reinstalls it every sweep.
- cron (container crontab):
  `* * * * * /opt/data/buzz-agents/supervise.sh --once >>/opt/data/buzz-agents/cron.log 2>&1`
- Logs: `/opt/data/buzz-agents/<profile>.log`, `supervise.log`, `cron.log`.

Everything under `/opt/data` survives a Hermes image update. The wrapper at
`/usr/local/bin/buzz` does not — the supervisor puts it back within a minute,
before any turn can complete.

## Why the wrapper

`buzz-acp` does not post the agent's reply; the agent runs
`buzz messages send --channel <uuid> --content …` itself (the channel UUID is in
the turn's `<context>`). Hermes's terminal sandbox strips every
`BUZZ_`-prefixed env var, so the agent cannot get `BUZZ_PRIVATE_KEY` from the
environment. `scripts/buzz-wrap.sh` reads the key from `<profile>.key` (0600)
and injects it into `buzz.real` only. It picks the profile from
`HERMES_PROFILE`, falling back to the basename of `HERMES_HOME` — both survive
the sandbox.

## Capability model (v1)

**`--respond-to owner-only` is the entire trust boundary.** Only the owner
(`f978cb69…`) can instruct an agent; everyone else in a channel sees the replies
but cannot drive them.

Agents otherwise run with **full Hermes capability** — shell, file edits,
`openbrain save`, `laptop_fs`, the full MCP toolset. `--permission-mode dont-ask`
is set in every env file to record intent, but Hermes ACP ignores it and the ACP
path runs `HERMES_YOLO_MODE=1`. This is acceptable only because the owner already
runs these same agents full-power on voice / WhatsApp / CLI — the Buzz surface
is not a new principal. Opening `--respond-to` past `owner-only` requires the
phase-2 approval bridge first.

Code pushes to protected branches are refused by the relay without the owner's
signed approval regardless of agent capability — configure branch protection on
any repo an agent can touch.

## Add an agent

1. `docker exec buzz-relay-1 /usr/local/bin/buzz-admin generate-key` — save the secret to the password manager (entry "Buzz agent — hermes/<profile>").
2. `cd /root/HermesPlusOpenbrain/deploy && docker compose -f docker-compose.buzz.yml exec relay /usr/local/bin/buzz-admin add-member --pubkey <hex> --role member`
3. `cp scripts/buzz-agent-env.example /opt/data/buzz-agents/<profile>.env`; set `BUZZ_PRIVATE_KEY`, replace `PROFILE` in `BUZZ_ACP_AGENT_ARGS`; `chmod 600`.
4. `printf '%s\n' '<hex-secret>' > /opt/data/buzz-agents/<profile>.key && chmod 600 /opt/data/buzz-agents/<profile>.key`
5. Add `<profile>` on its own line to `/opt/data/buzz-agents/enabled`.
6. `docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/buzz-agents/supervise.sh --once` (cron does it within a minute anyway).
7. In the desktop app: give the agent a display name (`Hermes · <profile>`) and add it to the channels it should see. `buzz-acp` auto-subscribes on the membership event.

## Remove / pause an agent

Delete its line from `enabled`, then
`kill $(cat /opt/data/buzz-agents/<profile>.pid)`.
Full removal: also
`docker exec buzz-relay-1 /usr/local/bin/buzz-admin remove-member --pubkey <hex>`
and shred `<profile>.key` / `<profile>.env`.

## Owner controls (in-channel, as owner)

`!cancel` / `!rotate` / `!shutdown` — kind:9 message, exact body, agent
mentioned via a separate `p`-tag.

## Rebuild buzz-acp / buzz (new upstream commit)

```bash
cd /root/buzz-src && git fetch && git checkout <new-sha>
rustup target add x86_64-unknown-linux-musl 2>/dev/null || true
cargo build --release -p buzz-acp -p buzz-cli --target x86_64-unknown-linux-musl
install -m755 target/x86_64-unknown-linux-musl/release/buzz-acp /docker/hermes-agent-7qpk/data/bin/buzz-acp
install -m755 target/x86_64-unknown-linux-musl/release/buzz    /docker/hermes-agent-7qpk/data/bin/buzz.real
# refresh /opt/data/bin/buzz-acp.version, then bounce the agents:
for p in $(grep -v '^#' /opt/data/buzz-agents/enabled); do kill "$(cat /opt/data/buzz-agents/$p.pid)" 2>/dev/null; done
# cron relaunches on the new binary within a minute
```

## After a Hermes image update — re-verify

1. `docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --version` runs.
2. `docker exec -u hermes -e HOME=/opt/data hermes-agent-7qpk-hermes-agent-1 /opt/hermes/.venv/bin/hermes -p default acp --check` still passes (the `-p` selector is unchanged).
3. Within a minute: one `buzz-acp` per enabled agent (`pgrep -af buzz-acp`) and `/usr/local/bin/buzz` is the wrapper (`head -3 /usr/local/bin/buzz`).
4. `@mention` one agent as owner in `#hermes` → it replies from its own identity.

## Phase 2

Per-action approval bridge (gate the agent's tool calls / `buzz messages send`
behind an owner `approve <id>` reply) · `--respond-to allowlist` when teammates
join the relay · a locked-down read-only profile (needs buzz-as-MCP so replies
don't require the terminal tool) · agents in more channels · agents that
initiate rather than only reply.
