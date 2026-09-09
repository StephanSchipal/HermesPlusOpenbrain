# Hermes-in-Buzz spike — findings (2026-09-09)

Spike for `docs/superpowers/plans/2026-09-09-hermes-buzz-agents.md` Task 1.
**Verdict: the naive `buzz-acp → hermes acp` bridge does not work as-is.**
Two concrete blockers, both plausibly fixable but needing another iteration.

## What worked

- `buzz-acp` built (musl-static, `block/buzz@3c7f288`, `cargo build -p buzz-acp
  --target x86_64-unknown-linux-musl` after `rustup target add`). 16 MB, runs
  fine inside the Hermes container. Also built the `buzz` CLI (`-p buzz-cli`,
  binary name `buzz`).
- Agent identity: `buzz-admin generate-key`, `buzz-admin add-member`. Profile
  selector for Hermes is **`hermes -p <profile> acp`** (the alias wrappers like
  `coder` are just `exec hermes -p coder "$@"`).
- Bridge connects: `wss://buzz.srv1608402.hstgr.cloud` from the Hermes
  container, NIP-42 auth as the member key (no `BUZZ_API_TOKEN` needed),
  `agent owner` gate active.
- Channel discovery: an agent isn't visible to add in the desktop app until it
  publishes a kind:0 profile (`buzz users set-profile --name …`). Channel
  membership has no relay API — the agent creates its own channel
  (`buzz channels create --type stream --visibility open`) and adds the owner
  (`buzz channels add-member --role owner`). buzz-acp auto-subscribes on the
  membership event.
- On @mention, Hermes `default` spins up with its **full toolset** — openbrain
  (15), stripe (14), laptop_fs (14) — model `claude-sonnet-5`, provider auth
  works. It **called `mcp__openbrain__search` successfully** and got real memory
  back.

## Blocker 1 — the agent cannot post its reply

`buzz-acp` never posts the agent's answer itself. Its model: the agent replies
by running **`buzz messages send`** (the `<base>` prompt instructs this).
`BUZZ_PRIVATE_KEY` is injected only into an **MCP-server subprocess**
(`--mcp-command`), *not* into the ACP agent's own environment.

Hermes's `terminal` tool runs in a **sandboxed "local environment / session
snapshot"** that does not inherit `BUZZ_PRIVATE_KEY`. The agent found and ran
`buzz` but every call returned
`{"error":"auth_error","message":"BUZZ_PRIVATE_KEY is required"}`. It then
looped retrying — 13 LLM calls, no reply, ~96k input tokens/call by the end.
Nothing reached `#hermes`.

Candidate fixes (need testing):
- Make Hermes's `terminal` tool pass `BUZZ_PRIVATE_KEY` through (Hermes
  `secrets` / `egress` credential injection, or a profile/terminal env
  passthrough setting).
- Provide the Buzz CLI to Hermes as an **MCP server** via
  `BUZZ_ACP_MCP_COMMAND` — requires a `buzz`-as-MCP shim (the `buzz` CLI has no
  `mcp` subcommand; would need `buzz-dev-mcp` or a small wrapper).
- Patch `buzz-acp` to post the agent's final `agent_message_chunk` text to the
  channel when the agent doesn't use the CLI (upstream change).

## Blocker 2 — `--permission-mode dont-ask` is ignored

buzz-acp sends the mode via `session/set_config_option` `configId:"mode"` —
which its own help notes is "for agents that support" it "(e.g.
`claude-agent-acp`)". **Hermes's ACP adapter does not implement it.** The agent
ran `terminal` freely (multiple `tool terminal completed` with real output).
The v1 capability model (rely on buzz-acp `dont-ask`) does not hold.

Candidate fixes:
- A dedicated **read-only Hermes profile** for Buzz with write/shell tools
  stripped (`-t`/toolset config, or profile tool allowlist) — don't rely on
  buzz-acp for the gate.
- `hermes acp` approval behaviour headless still unknown (it didn't block, it
  just executed) — a restricted profile sidesteps this entirely.

## Operational note

`docker exec -d … buzz-acp` is fragile — the bridge died once mid-spike
("shutting down" with no `!shutdown` sent; likely a container reconcile or the
exec losing its parent). Confirms the plan's cron/watchdog requirement, but the
supervisor needs to also cap runaway turns (`BUZZ_ACP_MAX_TURN_DURATION`,
`--max-turns-per-session`).

## Recommendation

Pause the build-out. This needs a second spike session focused on Blocker 1
(get one reply to actually land) and Blocker 2 (a locked-down Buzz profile),
then the plan can resume from Task 2. If those can't be solved cleanly, the
fallback is a purpose-built bridge (hold a Nostr identity, forward to
`hermes -z`/`hermes send`, post the result) rather than `buzz-acp`.

## VPS state left behind

- `/opt/data/bin/{buzz-acp,buzz,buzz-acp.version}` — the binaries (keep).
- `/opt/data/spike-default.env` (chmod 600) — spike agent key + config.
- `/opt/data/spike-default.log` — the spike turn log.
- Relay member `8b10b48045d1237b1705300cf2acd5f0505a686d49da79cc5ec221c6f44a884c`
  (spike agent) + channel `#hermes` (`aea9fa66-34f9-46fd-a6dd-4dbc2a95c776`),
  owner + agent as members. `buzz-admin remove-member` + `buzz channels delete`
  to fully clean, or reuse for the next spike.
- One discarded keypair was printed to the session transcript early on
  (`2019c677…` / never added as a member — inert).
