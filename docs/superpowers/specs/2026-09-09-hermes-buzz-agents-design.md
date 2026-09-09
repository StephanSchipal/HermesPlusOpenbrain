# Hermes agents in Buzz — design

**Date:** 2026-09-09
**Status:** approved, ready for implementation plan
**Owner:** Stephan Schipal
**Depends on:** [`2026-09-08-buzz-relay-deploy-design.md`](2026-09-08-buzz-relay-deploy-design.md) (the Buzz relay, live since 2026-09-09 — see [`buzz.md`](../../../buzz.md))

## 1. Goal

Make the existing Hermes agent profiles participate in Buzz channels as
first-class members, so real people and Hermes agents coordinate in the same
rooms — each agent answering with its real persona, model, OpenBrain memory,
and toolset.

Success criteria:

- A person `@mention`s a Hermes agent in a Buzz channel and gets a reply from
  that agent's identity, produced by the corresponding `hermes` profile.
- Adding another profile to the fleet is a one-line config change.
- The agents cannot mutate the live filesystem, run shell, or write memory in
  v1 (`dontAsk`); the one "agent works → owner approves → it lands" loop that is
  real end-to-end is **code review via Buzz feature-branch channels**.
- The setup survives a Hermes container image update (same `/opt/data` + cron
  pattern the voice gateway uses to persist across image updates).
- No regression to Hermes gateways (voice, WhatsApp, CLI) or to the Buzz relay.
- Operation is documented in `buzz-agents.md`.

Non-goals (v1): per-action approval for shell/edit/memory; agents in many
channels or auto-joining; agents initiating conversation; a remote-agent
substrate.

## 2. Context

### 2.1 The two sides (verified 2026-09-09)

**Hermes** — one container `hermes-agent-7qpk-hermes-agent-1`, image
`ghcr.io/hostinger/hvps-hermes-agent` (v0.20.5), on network
`hermes-agent-7qpk_default`. `$HERMES_HOME=/opt/data` (host:
`/docker/hermes-agent-7qpk/data`) holds all state/config/auth and is the
image-update-durable volume. 7 profiles:

| Profile | Model | Notes |
| --- | --- | --- |
| `default` | claude-sonnet-5 | richest toolset: voice, CLI, `laptop_fs`, OpenBrain MCP, Stripe MCP |
| `coder` | claude-sonnet-5 | alias `coder` |
| `designer` | claude-sonnet-5 | alias `designer` |
| `master` | claude-sonnet-5 | alias `master` |
| `openbrain` | moonshotai/kimi-k3 | WhatsApp capture bot |
| `researcher` | claude-sonnet-5 | alias `researcher` |
| `writer` | claude-sonnet-5 | alias `writer` |

`hermes acp` starts an **ACP server over stdio** ("for editor integration —
VS Code, Zed, JetBrains"). Flags seen: `--accept-hooks`, `--check`, `--setup`,
`--version`. **Profile selection mechanism for `hermes acp` is unconfirmed**
(`--profile` is not on `hermes acp` itself; candidates: a top-level flag, a
`HERMES_PROFILE` env var, or the alias wrapper scripts). Resolved in Task 0.

**Buzz relay** — live, `wss://buzz.srv1608402.hstgr.cloud`, containers
`buzz-relay-1` + deps, compose project `buzz`, network `buzz_buzz_net`. Closed
relay (`BUZZ_REQUIRE_RELAY_MEMBERSHIP=true`, `BUZZ_REQUIRE_AUTH_TOKEN=true`,
`BUZZ_ALLOW_NIP_OA_AUTH=true`). Relay's `buzz-admin` has `generate-key`,
`add-member`, `remove-member`, `list-members`, `reconcile-channels`.

### 2.2 `buzz-acp` (github.com/block/buzz, `crates/buzz-acp`)

The purpose-built bridge: *"ACP harness that connects AI agents to Buzz. The
harness listens for @mentions on the relay, prompts your agent, and the agent
replies using the Buzz CLI."* Supports "any agent that speaks ACP over stdio".

Config is all env vars (matching CLI flags):

| Var | Default | Use |
| --- | --- | --- |
| `BUZZ_PRIVATE_KEY` | — (**required**) | agent's Nostr nsec — relay auth + identity |
| `BUZZ_RELAY_URL` | `ws://localhost:3000` | relay WS URL |
| `BUZZ_ACP_AGENT_COMMAND` | `goose` | agent binary to spawn → **`hermes`** |
| `BUZZ_ACP_AGENT_ARGS` | `acp` | agent args (comma-split) → **`acp`** (+ profile selector once known) |
| `BUZZ_ACP_MCP_COMMAND` | `""` | optional extra MCP server — **unused** (Hermes carries its own) |
| `--permission-mode` (env name TBD, Task 0) | `bypass-permissions` | **must be set to `dont-ask`** |
| `--respond-to` / `BUZZ_ACP_RESPOND_TO` | `owner-only` | author gate — v1 keeps `owner-only` |
| `BUZZ_ACP_AGENT_OWNER` | — | owner pubkey (hex) — **required** for the gate to work |
| `--agents` / `BUZZ_ACP_AGENTS` | `1` | subprocesses per agent — keep `1` |
| `BUZZ_ACP_IDLE_TIMEOUT` | `620` | max seconds of agent silence before cancelling a turn |
| `BUZZ_ACP_MAX_TURN_DURATION` | `7200` | absolute per-turn wall-clock cap |
| `BUZZ_API_TOKEN` | — | "required if relay enforces token auth" — **source unconfirmed**, resolved in Task 0 |

Behaviour verified from source (`crates/buzz-acp/src/acp.rs`, `config.rs`):

- On `session/request_permission` from the agent, buzz-acp **auto-approves**
  (`allow_once`; falls back to `reject_once` if no allow option). **There is no
  post-to-channel / wait-for-owner approval flow.**
- `--permission-mode` (env likely `BUZZ_ACP_PERMISSION_MODE`; sent to the agent
  via `session/set_config_option`): `default` | `accept-edits` |
  `bypass-permissions` | **`dont-ask`** ("never prompt; reject anything that
  would require permission") | `plan` (no tool execution at all).
  **The buzz-acp default is `bypass-permissions`** (fully autonomous) — v1 must
  set `dont-ask` **explicitly** in every agent's config.
- **Author gate `--respond-to`** (env `BUZZ_ACP_RESPOND_TO`), default
  **`owner-only`**: forwards to the agent only events from
  `BUZZ_ACP_AGENT_OWNER`. Other values: `allowlist`
  (`BUZZ_ACP_RESPOND_TO_ALLOWLIST` = comma-sep hex pubkeys, owner always
  included), `anyone`, `nobody`. Disallowed authors are dropped before any
  subscription/mention logic. Owner control commands (`!shutdown`, `!cancel`,
  `!rotate` — kind:9, exact body, separate `p`-tag mention) are handled by the
  harness regardless of gate.
- `BUZZ_ACP_AGENT_OWNER` — the agent's registered owner pubkey. Without it,
  `owner-only`/`allowlist` drop everything.
- Discovers channels the agent is a **member** of (`GET /api/channels?member=true`);
  auto-subscribes on new membership.
- One `buzz-acp` process per agent (`--agents 1`); each needs its own keypair.
- Not published as a binary — built from the crate (`cargo build --release -p buzz-acp`).

### 2.3 Buzz-native code review (why "approve each action" works for git)

Buzz Projects: a repo announcement (`kind:30617`) carries `buzz-protect` tags
the **relay enforces at the git transport layer** — only listed npubs push to
protected branches, force-push blocked, merges need N signed approval events
(`kind:46011`). Agents inherit push access from their owner via NIP-OA. So an
agent pushing to `main` is impossible without the owner's signed approval; an
agent pushing a **feature branch** is fine and spawns a review channel with the
diff. This is the v1 capability boundary for code.

## 3. Design

### 3.1 Approach — in the live Hermes container, watchdog-supervised

`buzz-acp` spawns `hermes acp` as a child, so it must share the Hermes runtime
and `/opt/data`. Rejected alternatives:

- **Sidecar container** (own compose service, mounts `/opt/data`): cleaner and
  repo-consistent, but needs a custom image on top of the vendor Hermes image
  (rebuild on every Hostinger update) and an unresolved question about running
  Hermes against a read-only `/opt/data` with a separate writable state dir.
  Reconsider in a later phase if the in-container footprint proves fragile.
- **Slim bridge + `hermes acp` over a socket**: `hermes acp` is stdio-only, so
  this still needs a per-profile `socat` shim inside the Hermes container —
  more moving parts, no benefit.

### 3.2 Task 0 — the spike (throwaway, gates the plan)

Done by hand in the live Hermes container; nothing committed except notes:

1. Build `buzz-acp` as a **musl-static** binary (Rust build container or dev
   box, pinned to a `block/buzz` commit), copy to the Hermes container.
2. Relay: `buzz-admin generate-key` → one keypair; `buzz-admin add-member
   --pubkey <hex>`.
3. Run one bridge by hand for the `default` profile:
   `BUZZ_PRIVATE_KEY=<nsec> BUZZ_RELAY_URL=wss://buzz.srv1608402.hstgr.cloud
   BUZZ_ACP_AGENT_COMMAND=hermes BUZZ_ACP_AGENT_ARGS=acp[,<profile selector>]
   buzz-acp`.
4. Add that pubkey to a `#hermes` channel; `@mention` it; confirm a persona-
   correct reply produced by Hermes.

Must resolve before the plan proceeds:

- **Profile selection** for `hermes acp` (flag / env / alias wrapper).
- **`--permission-mode` env var name** and that `dont-ask` is honoured by
  `hermes acp` (i.e. Hermes actually rejects a write when asked).
- **`BUZZ_API_TOKEN`**: needed, or does member-key NIP-42 satisfy the HTTP
  bridge? If needed, where is it minted?
- **Headless approval**: does `hermes acp` block on a TTY prompt for any tool?
  `--accept-hooks` + `dontAsk` expected to make turns non-interactive — confirm.
- **Prompt layering**: `buzz-acp`'s injected `base_prompt.md` (Buzz CLI usage)
  vs Hermes `SOUL.md` — any contradiction that breaks replies.
- **Network**: `wss://` public route works from the Hermes container; decide
  whether production uses the public route or joins `buzz_buzz_net`.
- **Cost**: tokens for one idle agent over an hour, and one real turn.

If Task 0 shows the in-container approach is unworkable (e.g. `hermes acp`
can't be made non-interactive, or profile selection is impossible), stop and
escalate — the sidecar alternative or an upstream fix becomes the plan.

### 3.3 Deployment (post-spike)

All under `/opt/data` so a Hermes image update cannot remove it:

```
/opt/data/
  bin/buzz-acp                       # musl-static, pinned commit recorded in buzz-agents.md
  buzz-agents/
    enabled                          # newline list of profile names to run  (the "launch N of 7" knob)
    default.env  coder.env  …        # per-agent (chmod 600): BUZZ_PRIVATE_KEY, profile selector,
                                      #   BUZZ_RELAY_URL, BUZZ_ACP_AGENT_COMMAND=hermes,
                                      #   BUZZ_ACP_PERMISSION_MODE=dont-ask,
                                      #   BUZZ_ACP_RESPOND_TO=owner-only,
                                      #   BUZZ_ACP_AGENT_OWNER=<owner hex>,
                                      #   BUZZ_ACP_IDLE_TIMEOUT, BUZZ_API_TOKEN?
    supervise.sh                      # for each name in `enabled`: ensure one buzz-acp is running,
                                      #   restart with backoff on exit, structured log to buzz-agents/<name>.log
```

- **cron** entry (every minute) runs `supervise.sh` — the same
  liveness mechanism the voice gateway uses. `supervise.sh` is idempotent and
  self-locking (one supervisor, one `buzz-acp` per enabled agent).
- Repo artefacts (this is inside a vendor container, so **no compose file**):
  - `scripts/buzz-agent-supervise.sh` — the supervisor, copied to `/opt/data/buzz-agents/`
  - `scripts/buzz-agent-env.example` — the per-agent env template
  - `buzz-agents.md` — runbook: what it is, the spike results, the file layout,
    add/remove an agent, the `enabled` list, rebuild/upgrade `buzz-acp`,
    re-verify after a Hermes image update, cost notes
  - `README.md` — a row under the Buzz section linking `buzz-agents.md`

### 3.4 Identity & roster

- One keypair per agent (`buzz-admin generate-key`). nsec → the owner's
  password manager (one entry per agent, e.g. "Buzz agent — hermes/coder") **and**
  `/opt/data/buzz-agents/<profile>.env` (chmod 600, never committed).
- `buzz-admin add-member --pubkey <hex> --role member` per agent — one at a
  time, `sleep 1` between (roster is a single kind:13534 event).
- Display name per agent: `Hermes · <profile>` (set via the Buzz CLI on first
  run, or a profile field), so they read distinctly from Fizz/Honey/Pollen.

### 3.5 Capability model (v1)

Two independent gates, both set explicitly per agent:

**Who may address an agent — `--respond-to owner-only`.** The relay currently
has one human member (the owner). `owner-only` + `BUZZ_ACP_AGENT_OWNER=<owner
hex>` means only the owner drives the agents; everyone else in a channel sees
the replies but cannot instruct them. When teammates join the relay, the
documented step is `--respond-to allowlist` with their pubkeys (or `anyone` for
a fully open agent). This gate is what makes v1's read-exfil exposure a
non-issue: the only person who can make an agent read and echo something is the
owner.

**What an agent may do — `--permission-mode dont-ask`.** Set explicitly (the
buzz-acp default `bypass-permissions` is *fully autonomous* and must never be
used here). `dont-ask` → the agent structurally cannot run shell, edit the live
filesystem, `openbrain save`, or `laptop_fs` writes. It can read/search
(OpenBrain recall, web, read files), reason, draft, and discuss.
- **Code:** an agent may `git push` a **feature branch** (becomes a review
  channel with the diff). Protected branches (`main`, …) carry `buzz-protect`
  tags — the relay refuses the merge without the owner's signed `kind:46011`
  approval. Configure branch protection on any repo an agent can touch.
- Stripe MCP: read-only key already; no action needed.
- With `owner-only`, the read-and-echo exposure is limited to the owner. When
  the gate is later opened to `allowlist`/`anyone`, that exposure returns and is
  the trigger for building the phase-2 approval bridge.

### 3.6 Channels

Agents see only channels they are members of. v1: create `#hermes`, add the
launched agents. The owner adds an agent to further channels as desired
(`buzz-acp` auto-subscribes on the membership event). No auto-join.

### 3.7 Cost control

- `BUZZ_ACP_IDLE_TIMEOUT` and `BUZZ_ACP_MAX_TURN_DURATION` set per agent in its
  env file (defaults acceptable pending Task 0 numbers).
- Idle agents cost nothing (no LLM call until `@mention`); a turn is one Hermes
  session at the profile's model.
- Launch 1–2 via `enabled`; expand after observing real usage.

## 4. Phase 2 (not built)

- Per-action approval bridge: a `buzz-acp` change (fork or upstream PR) that
  routes `session/request_permission` to the channel and waits for the owner's
  `approve <id>` reply — unlocks shell/edit/memory under owner control.
- Auto-join rules / agents in many channels.
- Agents that initiate (cron-driven posts, watching CI) rather than only reply.
- Remote-agent substrate (Buzz's K8s provider) instead of in-container.
- Sidecar-container topology if the in-container footprint proves fragile.

## 5. Risks

| Risk | Mitigation |
| --- | --- |
| `hermes acp` blocks on a TTY approval prompt headless | Task 0 gates on this; `--accept-hooks` + `dontAsk` expected to suffice; escalate if not |
| Profile selection for `hermes acp` not possible | Task 0 gates on this; fall back to alias wrappers or a per-profile `HERMES_HOME` |
| Hermes image update removes the binary / stops the agents | `/opt/data` + cron pattern (proven by voice); `buzz-agents.md` has a re-verify checklist; cron re-launches within a minute |
| Prompt-injection via a channel member → data read + echo | `--respond-to owner-only` means only the owner can drive an agent in v1; `dont-ask` blocks all writes/exec regardless. Opening the gate later is the trigger for the phase-2 approval bridge |
| buzz-acp default `--permission-mode` is `bypass-permissions` (autonomous) | v1 sets `dont-ask` explicitly in every env file; Task 0 confirms the mode is applied (agent declines a write) |
| `buzz-acp` upstream changes break the pinned build | Commit pinned in `buzz-agents.md`; rebuild is a documented step |
| `BUZZ_API_TOKEN` unobtainable / bridge auth unclear | Task 0 resolves; NIP-42 member-key auth may already satisfy it |
| Two agents added to the roster same-second | Add one at a time, `sleep 1` (as with relay members) |
| LLM cost from several always-connected agents | Launch 1–2 first; idle cost is zero; per-agent turn caps |

## 6. Verification

1. Task 0 spike: one agent answers in `#hermes` with correct persona + a tool
   result (e.g. an OpenBrain recall).
2. Post-deploy: `enabled` lists the launch set; `supervise.sh` shows one
   `buzz-acp` per name; each agent authenticates (relay log `NIP-42 auth
   successful` for its pubkey).
3. `dont-ask` holds: ask an agent (as the owner) to write a file / run a
   command → it declines, nothing executes. `owner-only` holds: a message from
   a non-owner pubkey gets no agent response.
4. Code path: an agent pushes a feature branch on a test repo → review channel
   appears with the diff; a push to `main` is refused without a signed approval.
5. Regression: voice / WhatsApp / CLI gateways and the Buzz relay unaffected;
   `docker ps` shows no restarts.
6. Image-update drill (or documented): after a Hermes update, cron relaunches
   the agents from `/opt/data`; re-run step 2.
