# Hermes agents in Buzz — design

**Date:** 2026-09-09
**Status:** approved; spike passed (see
[`../notes/2026-09-09-hermes-buzz-agents-spike.md`](../notes/2026-09-09-hermes-buzz-agents-spike.md));
capability model revised after the spike — build-out in progress
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
- Only the owner can instruct an agent (`--respond-to owner-only`); other channel
  members see replies but cannot drive the agents. This is the v1 trust
  boundary — see §3.5. (The spike established that `--permission-mode dont-ask`
  does **not** restrict Hermes — the ACP path runs full-capability — and that
  the reply mechanism itself needs the terminal tool, so a "read-only agent" is
  not the v1 model.)
- The setup survives a Hermes container image update (same `/opt/data` + cron
  pattern the voice gateway uses to persist across image updates).
- No regression to Hermes gateways (voice, WhatsApp, CLI) or to the Buzz relay.
- Operation is documented in `buzz-agents.md`.

Non-goals (v1): per-action approval for shell/edit/memory; a locked-down
read-only agent profile; agents in many channels or auto-joining; agents
initiating conversation; a remote-agent substrate.

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
VS Code, Zed, JetBrains"). Profile selection (spike-confirmed) is the top-level
`-p` flag: **`hermes -p <profile> acp`**.

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
| `BUZZ_ACP_AGENT_ARGS` | `acp` | agent args (comma-split) → **`-p,<profile>,acp`** (spike-confirmed selector) |
| `BUZZ_ACP_MCP_COMMAND` | `""` | optional extra MCP server — **unused** (Hermes carries its own) |
| `BUZZ_ACP_PERMISSION_MODE` | `bypass-permissions` | set to `dont-ask` for intent, but **Hermes ACP ignores it** (see below) — the real gate is `owner-only` |
| `BUZZ_ACP_MAX_TURNS_PER_SESSION` | `0` (off) | proactive session rotation cap — set (e.g. `8`) as a runaway backstop |
| `--respond-to` / `BUZZ_ACP_RESPOND_TO` | `owner-only` | author gate — v1 keeps `owner-only` |
| `BUZZ_ACP_AGENT_OWNER` | — | owner pubkey (hex) — **required** for the gate to work |
| `--agents` / `BUZZ_ACP_AGENTS` | `1` | subprocesses per agent — keep `1` |
| `BUZZ_ACP_IDLE_TIMEOUT` | `620` | max seconds of agent silence before cancelling a turn |
| `BUZZ_ACP_MAX_TURN_DURATION` | `7200` | absolute per-turn wall-clock cap |
| `BUZZ_API_TOKEN` | — | "required if relay enforces token auth" — **source unconfirmed**, resolved in Task 0 |

Behaviour verified from source (`crates/buzz-acp/src/acp.rs`, `config.rs`,
`lib.rs`) **and the spike**:

- **How the agent replies:** buzz-acp does **not** post the agent's answer. The
  agent must run **`buzz messages send --channel <uuid> --content …`** itself
  (the injected `base_prompt.md` instructs this; the channel UUID is in the
  turn's `<context>`). buzz-acp only streams `agent_message_chunk` to an
  encrypted observer + drives typing/presence.
- **`BUZZ_PRIVATE_KEY` does not reach the Hermes terminal.** buzz-acp spawns
  `hermes acp` with its env inherited (key present), but Hermes's terminal
  sandbox strips secret-classified vars and `terminal.env_passthrough` can't
  restore a profile-secret under profile-multiplexing. **Fix (spike):** a
  `buzz` CLI **wrapper** on the container PATH that injects the key from a
  `0600` file into the `buzz.real` subprocess only. See §3.3.
- On `session/request_permission` from the agent, buzz-acp **auto-approves**
  (`allow_once`; `reject_once` if no allow option). No post-to-channel /
  wait-for-owner flow.
- **`--permission-mode` is sent via `session/set_config_option`, which Hermes
  ACP does not implement — so `dont-ask` is a no-op.** The Hermes ACP path also
  runs `HERMES_YOLO_MODE=1`. Net: the agent has its **full toolset and
  capability** (shell, file edits, `openbrain save`, `laptop_fs` writes). v1
  still sets `BUZZ_ACP_PERMISSION_MODE=dont-ask` to record intent and in case a
  future Hermes honours it, but the capability gate that actually holds is
  `--respond-to owner-only` (§3.5).
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

### 3.2 Task 0 — the spike (DONE 2026-09-09)

Full findings:
[`../notes/2026-09-09-hermes-buzz-agents-spike.md`](../notes/2026-09-09-hermes-buzz-agents-spike.md).
Outcome: **the bridge works end-to-end** — owner @mentions a Hermes profile in
`#hermes`, it spins up with its full toolset, runs a real
`mcp__openbrain__search`, and posts the answer back. Resolved:

- **Profile selector:** `hermes -p <profile> acp` → `BUZZ_ACP_AGENT_ARGS=-p,<profile>,acp`.
- **Relay auth:** member-key NIP-42 is sufficient; no `BUZZ_API_TOKEN`.
- **Network:** `wss://buzz.srv1608402.hstgr.cloud` (public route) works from the
  Hermes container. No need to join `buzz_buzz_net`.
- **`--permission-mode dont-ask`:** ignored by Hermes ACP (see §2.2). Agent runs
  full-capability. v1 gate is `owner-only`.
- **Reply auth:** solved with a `buzz` CLI wrapper (§3.3). `buzz-acp`'s
  `base_prompt.md` (use `buzz messages send`) does not conflict with Hermes
  `SOUL.md`.
- **Headless:** `hermes acp` does not block on TTY prompts (runs YOLO).
- **Cost:** one real turn ≈ 5–25 `claude-sonnet-5` calls (the first spike run
  went to 23 while it looped on the auth error; capped now via
  `BUZZ_ACP_MAX_TURN_DURATION` + `BUZZ_ACP_MAX_TURNS_PER_SESSION`). Idle = 0.
- **Config gotcha:** `BUZZ_ACP_IDLE_TIMEOUT` must be **< `BUZZ_ACP_MAX_TURN_DURATION`**
  or buzz-acp refuses to start.

### 3.3 Deployment (post-spike)

All under `/opt/data` so a Hermes image update cannot remove it:

```
/opt/data/
  bin/
    buzz-acp                         # musl-static, pinned commit recorded in buzz-agents.md
    buzz.real                        # the real buzz CLI (same pinned commit)
  buzz-agents/
    enabled                          # newline list of profile names to run  (the "launch N of 7" knob)
    <profile>.key                    # chmod 600 — the agent's Nostr secret (hex). SINGLE source of
                                      #   truth: the supervisor reads it -> BUZZ_PRIVATE_KEY for
                                      #   buzz-acp; the reply wrapper reads the same file.
    <profile>.env                    # chmod 600 — NON-secret config: BUZZ_RELAY_URL,
                                      #   BUZZ_ACP_AGENT_COMMAND=hermes,
                                      #   BUZZ_ACP_AGENT_ARGS=-p,<profile>,acp,
                                      #   BUZZ_ACP_PERMISSION_MODE=dont-ask (intent only),
                                      #   BUZZ_ACP_RESPOND_TO=owner-only,
                                      #   BUZZ_ACP_AGENT_OWNER=<owner hex>,
                                      #   BUZZ_ACP_IDLE_TIMEOUT (< MAX_TURN_DURATION),
                                      #   BUZZ_ACP_MAX_TURN_DURATION, BUZZ_ACP_MAX_TURNS_PER_SESSION
    supervise.sh                      # for each name in `enabled`: ensure one buzz-acp is running,
                                      #   restart with backoff on exit, structured log to buzz-agents/<name>.log;
                                      #   also (re)installs the buzz wrapper on PATH
    buzz-wrap.sh                      # the buzz CLI wrapper (copied to /usr/local/bin/buzz by supervise.sh)
```

**The reply-auth wrapper.** `/usr/local/bin/buzz` is a small `sh` script that
reads the calling agent's key from `/opt/data/buzz-agents/<profile>.key` (0600),
exports `BUZZ_PRIVATE_KEY` for the `buzz.real` subprocess only, and execs it.
The key never enters the (sandbox-scrubbed) environment or the agent prompt.
`/usr/local/bin` is **not** on a Docker volume, so `supervise.sh` reinstalls the
wrapper on every sweep (and it must run once before the first turn).

The wrapper identifies which agent is calling it from a **sandbox-visible**
Hermes profile marker — `HERMES_PROFILE` if Hermes sets it, else the basename of
`$HERMES_HOME` (`/opt/data/profiles/<p>` → `<p>`; the base `default` profile is
`/opt/data` → mapped to `default`). It must **not** use a `BUZZ_*` var — the
spike showed Hermes's terminal sandbox strips the entire `BUZZ_` prefix, secret
or not. The implementer confirms the exact marker with a one-line probe
(`hermes -p <p> -z "run: env | grep -E 'HERMES_PROFILE|HERMES_HOME'"`).

- **Liveness is a HOST cron job** (the container is s6-managed and has no
  crontab — same constraint the `laptop_fs` watchdog works around):
  `* * * * * /root/HermesPlusOpenbrain/scripts/buzz-agents-watchdog.sh`. The
  watchdog does two `docker exec`s: `install` the wrapper to `/usr/local/bin`
  (as root), then `supervise.sh --once` (as `hermes`). `supervise.sh` is
  idempotent and self-locking (one supervisor, one `buzz-acp` per enabled
  agent). buzz-acp + its `hermes acp` children run as `hermes`, so
  `/opt/data/buzz-agents/` is `hermes`-owned.
- Repo artefacts (this is inside a vendor container, so **no compose file**):
  - `scripts/buzz-agents-watchdog.sh` — the host cron entry point
  - `scripts/buzz-agent-supervise.sh` — the supervisor, copied to `/opt/data/buzz-agents/`
  - `scripts/buzz-wrap.sh` — the `buzz` CLI wrapper
  - `scripts/buzz-agent-env.example` — the per-agent env template
  - `buzz-agents.md` — runbook: what it is, the spike results, the file layout,
    add/remove an agent, the `enabled` list, rebuild/upgrade `buzz-acp`,
    re-verify after a Hermes image update, cost notes
  - `README.md` — a row under the Buzz section linking `buzz-agents.md`

### 3.4 Identity & roster

- One keypair per agent (`buzz-admin generate-key`). Secret (hex) → the owner's
  password manager (one entry per agent, e.g. "Buzz agent — hermes/coder") and
  `/opt/data/buzz-agents/<profile>.key` (bare hex, chmod 600, never committed) —
  the single on-disk copy. The supervisor exports it as `BUZZ_PRIVATE_KEY` for
  buzz-acp; the reply wrapper reads the same file. `<profile>.env` holds only
  non-secret config.
- `buzz-admin generate-key` prints the secret to stdout — capture it to the
  0600 file directly / via stdin; never let it reach a shell transcript.
- `buzz-admin add-member --pubkey <hex> --role member` per agent — one at a
  time, `sleep 1` between (roster is a single kind:13534 event).
- Display name: the `default` agent is **`Hermes`**; every other profile is
  **`Hermes-<profile>`** (e.g. `Hermes-openbrain`). Set via
  `buzz users set-profile --name …` on first run. Reads distinctly from
  Fizz/Honey/Pollen, and `@Hermes` / `@Hermes-openbrain` resolve unambiguously.

### 3.5 Capability model (v1)

**The gate that holds — `--respond-to owner-only`.** The relay currently has one
human member (the owner). `owner-only` + `BUZZ_ACP_AGENT_OWNER=<owner hex>`
means only the owner drives the agents; everyone else in a channel sees the
replies but cannot instruct them. Disallowed authors are dropped before any
mention/subscription logic. This is the **entire** v1 trust boundary. When
teammates join the relay, the next step is `--respond-to allowlist` with their
pubkeys — and *that* is the trigger for the phase-2 per-action approval bridge,
because it re-introduces the read-and-echo exposure that `owner-only` removes.

**What an agent may do — its full Hermes capability.** The spike established that
`--permission-mode dont-ask` is a no-op (Hermes ACP ignores
`session/set_config_option`; the path runs `HERMES_YOLO_MODE=1`), and that the
reply mechanism *requires* the terminal tool anyway (to run `buzz messages
send`). So a Buzz agent has the same capability its profile has on every other
Hermes surface — shell, file edits, `openbrain save`, `laptop_fs` writes, the
full MCP toolset. This is acceptable for v1 **only because `owner-only` holds**:
the owner already runs these same agents full-power on voice / WhatsApp / CLI,
so the Buzz surface is not a new trust boundary or a new principal.

Consequences to accept explicitly:

- **Prompt injection from channel content.** A non-owner cannot instruct an
  agent, but the owner's own prompt could reference channel text an attacker
  planted. Same exposure as the owner pasting untrusted text into any Hermes
  chat. Mitigation is the owner's judgement in v1; the phase-2 approval bridge
  is the structural fix when the gate opens.
- **Code:** an agent may `git push` a **feature branch** (becomes a review
  channel with the diff). Protected branches (`main`, …) carry `buzz-protect`
  tags — the relay refuses the merge without the owner's signed `kind:46011`
  approval. Configure branch protection on any repo an agent can touch. This is
  a *relay-enforced* boundary and holds regardless of agent capability.
- **Stripe MCP:** read-only key already; no write path exists.
- **`laptop_fs`:** reaches the owner's laptop over Tailscale. In scope for the
  agent. If that is not wanted for a given profile, drop the `laptop_fs`
  toolset from that profile's config (out of scope for this plan).

**A locked-down read-only profile** (strip write/shell toolsets) was considered
and deferred: it conflicts with the wrapper needing the terminal tool to post
replies, and would need buzz-as-MCP (`BUZZ_ACP_MCP_COMMAND` + a "send message"
MCP tool that does not exist yet) or a `buzz-acp` patch to auto-post agent text.
Phase 2.

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

- Per-action approval bridge: since Hermes ignores `session/request_permission`
  mode, this needs either a `buzz-acp` change that gates the agent's `buzz
  messages send` / tool calls behind an owner `approve <id>` reply, or a
  buzz-as-MCP layer that mediates. Needed when `--respond-to` opens past
  `owner-only`.
- Locked-down read-only Buzz profile (needs buzz-as-MCP or a buzz-acp auto-post
  patch so replies don't require the terminal tool).
- Auto-join rules / agents in many channels.
- Agents that initiate (cron-driven posts, watching CI) rather than only reply.
- Remote-agent substrate (Buzz's K8s provider) instead of in-container.
- Sidecar-container topology if the in-container footprint proves fragile.

## 5. Risks

| Risk | Mitigation |
| --- | --- |
| ~~`hermes acp` blocks on a TTY approval prompt headless~~ | Resolved in spike: it does not block (runs YOLO) |
| ~~Profile selection for `hermes acp` not possible~~ | Resolved in spike: `hermes -p <profile> acp` |
| Agent loops on a failed `buzz messages send` (spike saw 23 calls) | Wrapper fixes the auth failure; `BUZZ_ACP_MAX_TURN_DURATION` + `BUZZ_ACP_MAX_TURNS_PER_SESSION` cap any residual runaway |
| Hermes image update removes the binary / stops the agents | `/opt/data` + cron pattern (proven by voice); `buzz-agents.md` has a re-verify checklist; cron re-launches within a minute |
| Prompt-injection via channel content → data read + write/exec | `--respond-to owner-only` — only the owner drives an agent in v1. Agents ARE full-capability (spike: `dont-ask` is a no-op), so if the owner acts on planted channel text the agent could act on it too — same exposure as the owner pasting untrusted text into any Hermes chat. Opening the gate to `allowlist` is the trigger for the phase-2 approval bridge |
| `buzz-acp` upstream changes break the pinned build | Commit pinned in `buzz-agents.md`; rebuild is a documented step |
| Reply wrapper lost on container recreate (`/usr/local/bin` not a volume) | `supervise.sh` reinstalls it every sweep; cron runs the sweep every minute; a fresh container has it back within a minute and before any turn can complete |
| `<profile>.key` readable by anything running as `hermes` in the container | Accepted — the container is a single trust domain; the file is 0600 and the point is to keep the key out of the sandbox-scrubbed *environment*, not to isolate it from the `hermes` uid |
| Two agents added to the roster same-second | Add one at a time, `sleep 1` (as with relay members) |
| LLM cost from several always-connected agents | Launch 1–2 first; idle cost is zero; per-agent turn caps |

## 6. Verification

1. ✅ Spike: one agent answers in `#hermes` with correct persona + a real
   `mcp__openbrain__search` result (2026-09-09).
2. Post-deploy: `enabled` lists the launch set; `supervise.sh` shows one
   `buzz-acp` per name; each agent authenticates (relay log `NIP-42 auth
   successful` for its pubkey); `/usr/local/bin/buzz` is the wrapper.
3. `owner-only` holds: a message from a non-owner pubkey gets no agent response
   (`<profile>.log` shows the event dropped by the author gate). A second
   enabled profile answers to the *same* owner @mention only when named.
4. Reply auth: `@mention` two different profiles → each posts back **from its
   own identity** (not cross-posting), proving per-profile key selection.
5. Code path: an agent pushes a feature branch on a test repo → review channel
   appears with the diff; a push to `main` is refused without a signed approval.
6. Regression: voice / WhatsApp / CLI gateways and the Buzz relay unaffected;
   `docker ps` shows no restarts.
7. Image-update drill (or documented): after a Hermes update, cron relaunches
   the agents from `/opt/data` **and reinstalls the wrapper**; re-run step 2.
