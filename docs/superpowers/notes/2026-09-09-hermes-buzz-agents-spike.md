# Hermes-in-Buzz spike — findings (2026-09-09)

Spike for `docs/superpowers/plans/2026-09-09-hermes-buzz-agents.md` Task 1.

**Spike 1 verdict (superseded): the naive bridge does not work — two blockers.**
**Spike 2 verdict: the bridge WORKS end-to-end.** A Hermes profile, @mentioned
in `#hermes`, spins up with its full toolset, runs a real `mcp__openbrain__search`,
and posts its answer back to the channel (`{"accepted":true}`). Confirmed live by
the owner ("works now"). Blocker 1 is solved with a small wrapper (no fork).
Blocker 2 is a non-issue for the chosen v1 model.

## What worked (spike 1 + 2)

- `buzz-acp` + `buzz` CLI built (musl-static, `block/buzz@3c7f288`,
  `cargo build -p buzz-acp --target x86_64-unknown-linux-musl` after
  `rustup target add`). ~16 MB each, run fine inside the Hermes container.
- Agent identity: `buzz-admin generate-key` + `buzz-admin add-member`. Profile
  selector for Hermes is **`hermes -p <profile> acp`**.
- Bridge connects: `wss://buzz.srv1608402.hstgr.cloud` from the container,
  NIP-42 auth as the member key (no `BUZZ_API_TOKEN`), `agent owner` gate active.
- Channel discovery: an agent is invisible in the desktop "add people" list
  until it publishes a kind:0 profile (`buzz users set-profile --name …`).
  Channel membership has no relay API — the agent creates its own channel
  (`buzz channels create --type stream --visibility open`) and adds the owner
  (`buzz channels add-member --role owner`); buzz-acp auto-subscribes on the
  membership event.
- On @mention, Hermes spins up with its **full toolset** — openbrain, stripe,
  laptop_fs — model `claude-sonnet-5`, provider auth works. Real
  `mcp__openbrain__search` calls succeed and return live memory.
- `--respond-to owner-only` gate works (only the owner pubkey's @mentions
  trigger a turn).

## Blocker 1 — RESOLVED: agent could not post its reply

`buzz-acp` does not post the agent's answer itself. Its model: the agent replies
by running **`buzz messages send`** (the `<base>` prompt instructs this).

Root cause (corrected from spike 1): `BUZZ_PRIVATE_KEY` *does* reach the
`hermes acp` child — buzz-acp spawns it without `env_clear`, so the child
inherits buzz-acp's own env. But **Hermes's `terminal` tool sanitizes it out of
the sandbox**:

- `tools/environments/local.py` strips secret-classified env vars from the
  terminal child. `terminal.env_passthrough` (config.yaml) is the documented
  allowlist, BUT under **profile multiplexing** (always active in this
  deployment) `resolve_passthrough_value` → `agent.secret_scope.get_secret`
  returns the value from an *installed secret scope*, not `os.environ`. The
  terminal spawn path has no scope installed, so a passthrough-allowed
  profile-secret still resolves to empty. `env_passthrough` only helps vars
  that are ALSO `_is_global_env` (a tight allowlist: `HERMES_*` runtime,
  `PATH`/`HOME`, `TERMINAL_*`, relay ROUTING stamps — not credentials).
- Net: `BUZZ_PRIVATE_KEY` cannot be delivered to Hermes's terminal via env.
  Spike-1 conclusion ("key never reaches the agent") was wrong about the
  mechanism; the effect ("every `buzz` call → `auth_error`, agent loops") was
  right — 23 API calls, real openbrain searches, no reply posted.

**The fix that works — a `buzz` CLI wrapper:**

- `/usr/local/bin/buzz` (on PATH, ahead of nothing — `/opt/data/bin` is not on
  PATH) is a 12-line `sh` script. It reads the agent key from
  `/opt/data/.buzz-agent-key` (chmod 600, `hermes`-owned), exports
  `BUZZ_PRIVATE_KEY` **for the `buzz.real` subprocess only**, defaults
  `BUZZ_RELAY_URL`, and `exec`s `/opt/data/bin/buzz.real "$@"`.
- The key never lives in the environment the sandbox scrubs, and never in the
  agent prompt. The sandbox can read the 600 file because it runs as `hermes`.
- Verified: `hermes -p buzzspike -z "...run: buzz messages send --channel <id>
  --content ..."` → `{"accepted":true}`, and the full buzz-acp bridge →
  owner @mention → answer in `#hermes`.
- Source of the wrapper: `scripts/` in this repo (to be added when the plan
  resumes — currently only on the VPS + session scratchpad).

## Blocker 2 — NOT a blocker for v1

`--permission-mode dont-ask` is still ignored (Hermes ACP has no
`session/set_config_option`), and `HERMES_YOLO_MODE=1` is set on the ACP path,
so the agent runs with **full capability**. For the chosen v1 model that is
acceptable:

- `--respond-to owner-only` means only the owner can trigger the agent at all.
- These are the owner's own agents, already run full-power on WhatsApp / voice /
  CLI. The Buzz surface is not a new trust boundary.
- Stripe MCP key is read-only; code changes go through Buzz-native git review.

A locked-down read-only Buzz profile (write/shell tools stripped) remains a
possible later refinement, but note the tension: the Blocker-1 fix needs the
terminal/shell tool enabled so the agent can run `buzz messages send`. A truly
shell-free profile would need buzz-as-MCP (`BUZZ_ACP_MCP_COMMAND` +
`buzz-dev-mcp`, which is shell/file tools only — still no "send message" tool)
or a buzz-acp patch to auto-post agent text. Deferred.

## Operational notes

- `docker exec -d … buzz-acp` is fragile — the bridge died once mid-spike-1
  ("shutting down", no `!shutdown`). Confirms the plan's cron/watchdog
  requirement; the supervisor must also cap runaway turns
  (`BUZZ_ACP_MAX_TURN_DURATION`, `BUZZ_ACP_MAX_TURNS_PER_SESSION`).
- Config gotcha: `BUZZ_ACP_IDLE_TIMEOUT` must be **< `BUZZ_ACP_MAX_TURN_DURATION`**
  or buzz-acp refuses to start.
- `buzz-acp --env-file <hostpath>` via `docker exec -d` is a clean launch (no
  `set -a` / heredoc needed).
- Two secret leaks into the session transcript during spike 2 (see below).

## Recommendation

**Resume the plan from Task 2.** The design (`…-design.md`) needs one revision:
replace the "`--permission-mode dont-ask` + env-delivered key" capability model
with (a) the `buzz` wrapper for reply auth and (b) "full capability, gated by
`owner-only`" for v1. Everything else in the plan (scripts, supervisor, PR, VPS
deploy tasks) stands.

Before production: **the spike agent identity must be rotated** — its private
key leaked to the transcript (below). Generate a fresh agent key, `add-member`
it, discard `8b10b48…`.

## VPS state left behind (spike 2)

- `/opt/data/bin/{buzz-acp,buzz.real,buzz,buzz-acp.version}` — binaries (keep).
  `buzz` and `buzz.real` are identical copies of the real CLI.
- `/usr/local/bin/buzz` — the wrapper script (NOT on a volume — lost on
  container recreate; the supervisor/deploy must reinstall it).
- `/opt/data/.buzz-agent-key` (chmod 600) — the agent hex key.
- `/opt/data/spike-buzzspike.env` (has `BUZZ_PRIVATE_KEY`) + `/opt/data/spike-buzzspike.log`.
- `/opt/data/spike-default.env` / `.log` — spike 1 leftovers.
- Hermes profile `buzzspike` (clone of `default`; `terminal.env_passthrough`
  was set during debugging — harmless, can revert; `BUZZ_*` added to its
  `.env` — redundant now, the wrapper is authoritative).
- Relay member `8b10b48045d1237b1705300cf2acd5f0505a686d49da79cc5ec221c6f44a884c`
  (spike agent, **key compromised — rotate**) + channel `#hermes`
  (`aea9fa66-34f9-46fd-a6dd-4dbc2a95c776`), owner + agent as members.
- bridge launched via:
  `docker exec -d --env-file /docker/hermes-agent-7qpk/data/spike-buzzspike.env
  -w /opt/data <container> sh -c '/opt/data/bin/buzz-acp > /opt/data/spike-buzzspike.log 2>&1'`

## Secret leaks (session transcript)

1. Spike 1: a discarded keypair printed early (`2019c677…`) — never
   `add-member`'d, inert.
2. Spike 2: the spike agent's **private key** (`23a2…56f2`, pubkey
   `8b10b48…`) printed twice via `cat -A` / `awk` on the env file. Also the
   `buzzspike` profile's `API_SERVER_KEY` (cloned from `default`) printed once
   — if `default`/`openbrain` share that value, rotate it.
   Mitigation: rotate the spike agent key before production; the agent owns
   nothing and can only post to one channel on the owner's own relay.
