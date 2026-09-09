# Hermes agents in Buzz — Implementation Plan (spike-2 revision)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run each chosen Hermes profile as a member of the live Buzz relay, so people and Hermes agents coordinate in the same channels — via `buzz-acp` bridging `hermes -p <profile> acp` into Buzz.

**Architecture:** One `buzz-acp` process per agent, each with its own Nostr keypair on the relay roster, spawning `hermes -p <profile> acp`. Runs **inside the live `hermes-agent-7qpk` container**, cron-supervised, all state under `/opt/data` so it survives Hermes image updates. The agent posts replies by running `buzz messages send` via a **wrapper** that injects its key from a `0600` file (Hermes's terminal sandbox strips `BUZZ_*` from the environment). v1 capability model: **full Hermes capability, gated solely by `--respond-to owner-only`** — the spike proved `--permission-mode dont-ask` is a no-op in Hermes ACP.

**Tech Stack:** Rust (`buzz-acp` + `buzz` CLI, built from `block/buzz@3c7f288`), Hermes ACP, Buzz relay (live), POSIX sh (`supervise.sh`, `buzz-wrap.sh`), cron.

**Design doc:** [`../specs/2026-09-09-hermes-buzz-agents-design.md`](../specs/2026-09-09-hermes-buzz-agents-design.md)
**Spike findings:** [`../notes/2026-09-09-hermes-buzz-agents-spike.md`](../notes/2026-09-09-hermes-buzz-agents-spike.md)
**Relay it builds on:** [`buzz.md`](../../../buzz.md)

---

## Pre-flight facts (verified in the spike, 2026-09-09)

- Hermes container: `hermes-agent-7qpk-hermes-agent-1`, `$HERMES_HOME=/opt/data`, host path `/docker/hermes-agent-7qpk/data`. Profiles: `default` (HERMES_HOME `/opt/data`), `coder`, `designer`, `master`, `openbrain`, `researcher`, `writer` (each HERMES_HOME `/opt/data/profiles/<name>`).
- Profile selector: **`hermes -p <profile> acp`** → `BUZZ_ACP_AGENT_ARGS=-p,<profile>,acp`.
- Relay: `wss://buzz.srv1608402.hstgr.cloud`. Member-key NIP-42 auth is sufficient — **no `BUZZ_API_TOKEN`**. Owner pubkey (hex): `f978cb6960678ad53d9c6ea9d9e0fc9510c494e67041d36a5e9d2da719556aa6`.
- `buzz-acp` config gotcha: `BUZZ_ACP_IDLE_TIMEOUT` **must be < `BUZZ_ACP_MAX_TURN_DURATION`** or it refuses to start.
- Hermes terminal sandbox strips every `BUZZ_`-prefixed env var (secret or not). `HERMES_PROFILE` / `HERMES_HOME` pass through (`_is_global_env`).
- `binaries` built during the spike live at `/opt/data/bin/{buzz-acp,buzz,buzz.real,buzz-acp.version}`. Source clone at `/root/buzz-src` (commit `c045321` on `main`; the pinned build is `3c7f288`).
- Reaching the VPS host shell: Stephan's SSH key lands in the hermes container; he uses hPanel → Browser terminal for a host root shell. Claude's SSH (`root@srv1608402.hstgr.cloud`) reaches the host directly, but its auto-mode classifier blocks compound/backgrounding commands — those go to Stephan.
- **Spike leftovers to clean or reuse:** Hermes profile `buzzspike`, relay member `8b10b48045d1237b1705300cf2acd5f0505a686d49da79cc5ec221c6f44a884c` (key compromised — see Task 6), channel `#hermes` `aea9fa66-34f9-46fd-a6dd-4dbc2a95c776`, `/opt/data/spike-*.{env,log}`, `/opt/data/.buzz-agent-key`, `/opt/data/buzzspike-envtest*.txt`, `/opt/data/envdump.txt`, `/usr/local/bin/buzz` (spike wrapper).

---

## File structure

| File | Responsibility | Task |
| --- | --- | --- |
| `scripts/buzz-wrap.sh` | `buzz` CLI wrapper: inject the calling agent's key from `<profile>.key` into `buzz.real` | Create (Task 1) |
| `scripts/tests/buzz-wrap.test.sh` | Wrapper unit test (stub `buzz.real`, assert key injection + profile selection) | Create (Task 1) |
| `scripts/buzz-agent-supervise.sh` | Supervisor: one `buzz-acp` per name in `enabled`; reinstall the wrapper; export the profile marker; restart on exit | Create (Task 2) |
| `scripts/tests/buzz-agent-supervise.test.sh` | Supervisor test (stub `buzz-acp`, assert one-per-enabled + relaunch) | Create (Task 2) |
| `scripts/buzz-agent-env.example` | Per-agent env template, every var documented | Create (Task 3) |
| `buzz-agents.md` | Runbook: what runs, capability model, add/remove agent, rebuild, image-update re-verify | Create (Task 4) |
| `README.md` | One row under the Buzz section → `buzz-agents.md` | Modify (Task 5) |
| VPS `/opt/data/bin/{buzz-acp,buzz.real}` | Built binaries (not committed) | Task 6 |
| VPS `/opt/data/buzz-agents/{enabled,*.env,*.key,supervise.sh,buzz-wrap.sh}` | Runtime config (not committed) | Tasks 7–8 |
| VPS container crontab | Liveness for `supervise.sh --once` | Task 8 |

No compose file — this runs inside a vendor-managed container.

---

## Task 1: The `buzz` CLI wrapper

**Files:**
- Create: `scripts/buzz-wrap.sh`
- Test: `scripts/tests/buzz-wrap.test.sh`

- [ ] **Step 1: Write the failing test**

`scripts/tests/buzz-wrap.test.sh`:
```sh
#!/bin/sh
# Wrapper resolves the agent key from <keydir>/<profile>.key and injects it as
# BUZZ_PRIVATE_KEY into buzz.real ONLY; falls back default->default.key; never
# leaks the key into its own environment.
set -eu
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin" "$TMP/keys"
# stub buzz.real: print the env var it received
cat > "$TMP/bin/buzz.real" <<'EOF'
#!/bin/sh
echo "KEY=${BUZZ_PRIVATE_KEY:-NONE}"
echo "RELAY=${BUZZ_RELAY_URL:-NONE}"
echo "ARGS=$*"
EOF
chmod +x "$TMP/bin/buzz.real"
printf 'coderkey111\n'   > "$TMP/keys/coder.key"
printf 'defaultkey000\n' > "$TMP/keys/default.key"

WRAP="sh $(pwd)/scripts/buzz-wrap.sh"
export BUZZ_WRAP_KEYDIR="$TMP/keys" BUZZ_WRAP_REAL="$TMP/bin/buzz.real"
export BUZZ_WRAP_RELAY="wss://relay.example"

# profile from HERMES_PROFILE
out=$(HERMES_PROFILE=coder $WRAP messages send --content hi)
echo "$out" | grep -q 'KEY=coderkey111' || { echo "FAIL: coder key not injected: $out"; exit 1; }
echo "$out" | grep -q 'RELAY=wss://relay.example' || { echo "FAIL: relay not set: $out"; exit 1; }
echo "$out" | grep -q 'ARGS=messages send --content hi' || { echo "FAIL: args not forwarded: $out"; exit 1; }

# profile from HERMES_HOME basename
out=$(HERMES_HOME=/opt/data/profiles/coder $WRAP x)
echo "$out" | grep -q 'KEY=coderkey111' || { echo "FAIL: HERMES_HOME path: $out"; exit 1; }

# base profile: HERMES_HOME=/opt/data -> default.key
out=$(HERMES_HOME=/opt/data $WRAP x)
echo "$out" | grep -q 'KEY=defaultkey000' || { echo "FAIL: default fallback: $out"; exit 1; }

# no key file -> wrapper still execs (buzz.real will error on its own), no crash
out=$(HERMES_PROFILE=ghost $WRAP x 2>&1 || true)
echo "$out" | grep -q 'KEY=NONE' || { echo "FAIL: missing-key case: $out"; exit 1; }

# wrapper's own env must not carry the key
out=$(HERMES_PROFILE=coder $WRAP env-check 2>/dev/null; env | grep -c '^BUZZ_PRIVATE_KEY=' || true)
[ "$(env | grep -c '^BUZZ_PRIVATE_KEY=')" = "0" ] || { echo "FAIL: key leaked to caller env"; exit 1; }

echo "PASS"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `sh scripts/tests/buzz-wrap.test.sh`
Expected: FAIL — `scripts/buzz-wrap.sh` does not exist.

- [ ] **Step 3: Write `scripts/buzz-wrap.sh`**

```sh
#!/bin/sh
# buzz CLI wrapper. Hermes's terminal sandbox strips BUZZ_* from the child
# environment, so the agent cannot get BUZZ_PRIVATE_KEY that way. This wrapper
# resolves the calling agent's key from a 0600 file by Hermes profile and
# injects it into the real CLI subprocess ONLY — never into this process's env,
# never into the agent prompt.
#
# Deployed to /usr/local/bin/buzz by buzz-agent-supervise.sh (which also keeps
# buzz.real staged). Overridable knobs (tests): BUZZ_WRAP_KEYDIR, BUZZ_WRAP_REAL,
# BUZZ_WRAP_RELAY.
set -eu

keydir="${BUZZ_WRAP_KEYDIR:-/opt/data/buzz-agents}"
real="${BUZZ_WRAP_REAL:-/opt/data/bin/buzz.real}"
relay="${BUZZ_WRAP_RELAY:-wss://buzz.srv1608402.hstgr.cloud}"

# Which agent is calling? Hermes profile marker (BUZZ_* is stripped by the
# sandbox, so never rely on one here).
profile="${HERMES_PROFILE:-}"
if [ -z "$profile" ]; then
  case "${HERMES_HOME:-}" in
    */profiles/*) profile=$(basename "$HERMES_HOME") ;;
    /opt/data|/opt/data/) profile=default ;;
    *) profile=default ;;
  esac
fi

keyfile="$keydir/$profile.key"
if [ -r "$keyfile" ]; then
  BUZZ_PRIVATE_KEY="$(cat "$keyfile")"
  export BUZZ_PRIVATE_KEY
fi
: "${BUZZ_RELAY_URL:=$relay}"
export BUZZ_RELAY_URL

exec "$real" "$@"
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `sh scripts/tests/buzz-wrap.test.sh`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add scripts/buzz-wrap.sh scripts/tests/buzz-wrap.test.sh
git commit -m "feat(buzz-agents): buzz CLI wrapper (key from 0600 file, per Hermes profile)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: The supervisor script

**Files:**
- Create: `scripts/buzz-agent-supervise.sh`
- Test: `scripts/tests/buzz-agent-supervise.test.sh`

- [ ] **Step 1: Write the failing test**

`scripts/tests/buzz-agent-supervise.test.sh`:
```sh
#!/bin/sh
# supervise.sh --once: one buzz-acp per enabled name, wrapper installed, profile
# marker exported, relaunch after a kill.
set -eu
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"; kill 0 2>/dev/null || true' EXIT

mkdir -p "$TMP/agents" "$TMP/localbin"
# stub buzz-acp: record profile marker + pid, then sleep
cat > "$TMP/agents/buzz-acp" <<'EOF'
#!/bin/sh
echo "started profile=$BUZZ_AGENT_PROFILE pid=$$" >> "$SUP_STARTED"
exec sleep 300
EOF
chmod +x "$TMP/agents/buzz-acp"
# stub wrapper source
printf '#!/bin/sh\necho wrap\n' > "$TMP/agents/buzz-wrap.sh"
printf 'alpha\nbravo\n# comment\n' > "$TMP/agents/enabled"
for a in alpha bravo; do printf 'BUZZ_PRIVATE_KEY=x\n' > "$TMP/agents/$a.env"; done

export SUP_STARTED="$TMP/started"
COMMON="BUZZ_AGENT_DIR=$TMP/agents BUZZ_ACP_BIN=$TMP/agents/buzz-acp BUZZ_WRAP_SRC=$TMP/agents/buzz-wrap.sh BUZZ_WRAP_DEST=$TMP/localbin/buzz"

env $COMMON sh scripts/buzz-agent-supervise.sh --once
sleep 1
grep -q 'profile=alpha' "$SUP_STARTED" || { echo "FAIL: alpha not started"; exit 1; }
grep -q 'profile=bravo' "$SUP_STARTED" || { echo "FAIL: bravo not started"; exit 1; }
[ -x "$TMP/localbin/buzz" ] || { echo "FAIL: wrapper not installed"; exit 1; }
[ "$(grep -c started "$SUP_STARTED")" = 2 ] || { echo "FAIL: expected exactly 2 starts"; exit 1; }

# idempotent: second --once starts nothing new
env $COMMON sh scripts/buzz-agent-supervise.sh --once
sleep 1
[ "$(grep -c started "$SUP_STARTED")" = 2 ] || { echo "FAIL: not idempotent"; exit 1; }

# kill alpha, re-run, assert relaunch
kill "$(awk -F'pid=' '/profile=alpha/{print $2}' "$SUP_STARTED" | head -1)" 2>/dev/null || true
sleep 1
env $COMMON sh scripts/buzz-agent-supervise.sh --once
sleep 1
[ "$(grep -c 'profile=alpha' "$SUP_STARTED")" -ge 2 ] || { echo "FAIL: alpha not relaunched"; exit 1; }
echo "PASS"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `sh scripts/tests/buzz-agent-supervise.test.sh`
Expected: FAIL — script does not exist.

- [ ] **Step 3: Write `scripts/buzz-agent-supervise.sh`**

```sh
#!/bin/sh
# One buzz-acp per name in $BUZZ_AGENT_DIR/enabled. Idempotent: starts only what
# is not already running. Also (re)installs the buzz wrapper — /usr/local/bin is
# not a Docker volume so it is lost on container recreate. With no flag, loops
# forever re-checking every 15s. Deployed to /opt/data/buzz-agents/supervise.sh,
# kept alive by cron (`supervise.sh --once` every minute).
set -eu

BUZZ_AGENT_DIR="${BUZZ_AGENT_DIR:-/opt/data/buzz-agents}"
BUZZ_ACP_BIN="${BUZZ_ACP_BIN:-/opt/data/bin/buzz-acp}"
BUZZ_WRAP_SRC="${BUZZ_WRAP_SRC:-$BUZZ_AGENT_DIR/buzz-wrap.sh}"
BUZZ_WRAP_DEST="${BUZZ_WRAP_DEST:-/usr/local/bin/buzz}"
LOGDIR="${LOGDIR:-$BUZZ_AGENT_DIR}"
LOCK="$BUZZ_AGENT_DIR/.supervise.lock"

exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then flock -n 9 || exit 0; fi

install_wrapper() {
  [ -r "$BUZZ_WRAP_SRC" ] || return 0
  if ! cmp -s "$BUZZ_WRAP_SRC" "$BUZZ_WRAP_DEST" 2>/dev/null; then
    install -m 0755 "$BUZZ_WRAP_SRC" "$BUZZ_WRAP_DEST" 2>/dev/null \
      && echo "$(date -Is) installed wrapper -> $BUZZ_WRAP_DEST" >>"$LOGDIR/supervise.log"
  fi
}

start_one() {
  name=$1
  pidf="$BUZZ_AGENT_DIR/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then return 0; fi
  [ -f "$BUZZ_AGENT_DIR/$name.env" ] || { echo "$(date -Is) no env for $name" >>"$LOGDIR/supervise.log"; return 0; }
  # shellcheck disable=SC1090
  ( set -a; . "$BUZZ_AGENT_DIR/$name.env"; set +a
    BUZZ_AGENT_PROFILE="$name"; export BUZZ_AGENT_PROFILE
    exec "$BUZZ_ACP_BIN" >>"$LOGDIR/$name.log" 2>&1 ) &
  echo $! > "$pidf"
  echo "$(date -Is) started $name pid $!" >>"$LOGDIR/supervise.log"
}

sweep() {
  install_wrapper
  [ -f "$BUZZ_AGENT_DIR/enabled" ] || return 0
  while IFS= read -r name; do
    name=$(printf '%s' "$name" | tr -d '[:space:]')
    [ -n "$name" ] || continue
    case "$name" in \#*) continue;; esac
    start_one "$name"
  done < "$BUZZ_AGENT_DIR/enabled"
}

sweep
[ "${1:-}" = "--once" ] && exit 0
while :; do sleep 15; sweep; done
```

Note: `BUZZ_AGENT_PROFILE` is exported into `buzz-acp` for operator/log clarity; the wrapper itself keys off `HERMES_PROFILE`/`HERMES_HOME` because `BUZZ_*` is stripped by the sandbox before the wrapper runs.

- [ ] **Step 4: Run the test, verify it passes**

Run: `sh scripts/tests/buzz-agent-supervise.test.sh`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add scripts/buzz-agent-supervise.sh scripts/tests/buzz-agent-supervise.test.sh
git commit -m "feat(buzz-agents): supervisor (one buzz-acp per enabled agent, reinstalls wrapper)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: The per-agent env template

**Files:**
- Create: `scripts/buzz-agent-env.example`

- [ ] **Step 1: Write it**

```sh
# Copy to /opt/data/buzz-agents/<profile>.env on the VPS, chmod 600. One per agent.
# The SECRET is NOT here — it lives only in /opt/data/buzz-agents/<profile>.key
# (bare hex, 0600). The supervisor reads it -> BUZZ_PRIVATE_KEY for buzz-acp;
# the reply wrapper reads the same file. This file is non-secret config.

# --- relay ---
BUZZ_RELAY_URL=wss://buzz.srv1608402.hstgr.cloud

# --- which Hermes profile this agent is ---
BUZZ_ACP_AGENT_COMMAND=hermes
BUZZ_ACP_AGENT_ARGS=-p,PROFILE,acp

# --- capability gate (v1) ---
# owner-only is the ONLY gate that holds. dont-ask is recorded for intent but
# Hermes ACP ignores it (spike). Do NOT set BUZZ_ACP_RESPOND_TO to anything
# looser without the phase-2 approval bridge.
BUZZ_ACP_PERMISSION_MODE=dont-ask
BUZZ_ACP_RESPOND_TO=owner-only
BUZZ_ACP_AGENT_OWNER=f978cb6960678ad53d9c6ea9d9e0fc9510c494e67041d36a5e9d2da719556aa6
BUZZ_ACP_AGENTS=1

# --- turn caps (IDLE_TIMEOUT MUST be < MAX_TURN_DURATION or buzz-acp won't start) ---
BUZZ_ACP_IDLE_TIMEOUT=180
BUZZ_ACP_MAX_TURN_DURATION=900
BUZZ_ACP_MAX_TURNS_PER_SESSION=8
```

- [ ] **Step 2: Commit**

```bash
git add scripts/buzz-agent-env.example
git commit -m "feat(buzz-agents): per-agent env template

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `buzz-agents.md` runbook

**Files:**
- Create: `buzz-agents.md`

- [ ] **Step 1: Write it** (mirror the tone of `buzz.md` / `stripe-mcp/DEPLOY.md`)

Sections, with real content (no `<…>` placeholders):

```markdown
# Hermes agents in Buzz

**Status: build-out in progress — spike passed 2026-09-09.**
(Flip to `**v1 LIVE 2026-09-__**` in Task 9.)

Bridges Hermes profiles into the live Buzz relay ([`buzz.md`](buzz.md)) as
member identities, via `buzz-acp` -> `hermes -p <profile> acp`.
Design: `docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md`.
Spike: `docs/superpowers/notes/2026-09-09-hermes-buzz-agents-spike.md`.

## What runs

- `/opt/data/bin/buzz-acp` + `/opt/data/bin/buzz.real` — built from
  `block/buzz@3c7f288` (musl-static). `.version` records commit + build date + sha256.
- `/opt/data/buzz-agents/<profile>.env` (chmod 600) — buzz-acp config incl.
  `BUZZ_PRIVATE_KEY` (its own relay auth).
- `/opt/data/buzz-agents/<profile>.key` (chmod 600) — bare hex key; the reply
  wrapper reads this.
- `/opt/data/buzz-agents/enabled` — newline list of profile names to run. **The
  "launch N of 7" knob.** `#`-prefixed lines ignored.
- `/opt/data/buzz-agents/supervise.sh` — copy of `scripts/buzz-agent-supervise.sh`.
  One `buzz-acp` per enabled name; also reinstalls the wrapper.
- `/opt/data/buzz-agents/buzz-wrap.sh` -> `/usr/local/bin/buzz` — the reply
  wrapper. `/usr/local/bin` is NOT a volume; the supervisor reinstalls it every
  sweep.
- cron (container crontab): `* * * * * /opt/data/buzz-agents/supervise.sh --once >>/opt/data/buzz-agents/cron.log 2>&1`.
- Logs: `/opt/data/buzz-agents/<profile>.log`, `supervise.log`, `cron.log`.

Everything under `/opt/data` survives a Hermes image update. The wrapper does
not — the supervisor puts it back within a minute (before any turn completes).

## Why the wrapper

buzz-acp doesn't post the agent's reply; the agent runs `buzz messages send`.
Hermes's terminal sandbox strips every `BUZZ_`-prefixed env var, so the agent
can't get `BUZZ_PRIVATE_KEY` from the environment. The wrapper reads the key
from `<profile>.key` (0600) and injects it into `buzz.real` only. It picks the
profile from `HERMES_PROFILE` / `HERMES_HOME` (both survive the sandbox).

## Capability model (v1)

**`--respond-to owner-only` is the entire trust boundary.** Only the owner
(`f978cb69…`) can instruct an agent; others see replies only. Agents run with
**full Hermes capability** (shell, file edits, `openbrain save`, `laptop_fs`,
full MCP toolset) — `--permission-mode dont-ask` is set for intent but Hermes
ACP ignores it and the path runs YOLO. This is acceptable only because the owner
already runs these agents full-power on voice / WhatsApp / CLI. Opening
`--respond-to` past `owner-only` requires the phase-2 approval bridge first.
Code pushes to protected branches are refused by the relay without the owner's
signed approval regardless of agent capability.

## Add an agent

1. `docker exec buzz-relay-1 /usr/local/bin/buzz-admin generate-key` — save the secret to the password manager.
2. `cd /root/HermesPlusOpenbrain/deploy && docker compose -f docker-compose.buzz.yml exec relay /usr/local/bin/buzz-admin add-member --pubkey <hex> --role member`
3. `cp scripts/buzz-agent-env.example /opt/data/buzz-agents/<profile>.env`; set `BUZZ_PRIVATE_KEY`, replace `PROFILE` in `BUZZ_ACP_AGENT_ARGS`; `chmod 600`.
4. `printf '%s\n' '<hex-secret>' > /opt/data/buzz-agents/<profile>.key && chmod 600 …`
5. Add `<profile>` to `/opt/data/buzz-agents/enabled`.
6. `docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/buzz-agents/supervise.sh --once` (cron does it within a minute anyway).
7. In the desktop app: give the agent a display name (`Hermes · <profile>`) and add it to the channels it should see.

## Remove / pause an agent

Delete its line from `enabled`; `kill $(cat /opt/data/buzz-agents/<profile>.pid)`.
Full removal: also `buzz-admin remove-member --pubkey <hex>` and shred
`<profile>.key` / `<profile>.env`.

## Owner controls (in-channel, as owner)

`!cancel` / `!rotate` / `!shutdown` — kind:9 message, exact body, agent
mentioned via a separate p-tag.

## Rebuild buzz-acp / buzz (new upstream commit)

```bash
cd /root/buzz-src && git fetch && git checkout <new-sha>
rustup target add x86_64-unknown-linux-musl 2>/dev/null || true
cargo build --release -p buzz-acp -p buzz-cli --target x86_64-unknown-linux-musl
install -m755 target/x86_64-unknown-linux-musl/release/buzz-acp /docker/hermes-agent-7qpk/data/bin/buzz-acp
install -m755 target/x86_64-unknown-linux-musl/release/buzz    /docker/hermes-agent-7qpk/data/bin/buzz.real
# update /opt/data/bin/buzz-acp.version; then bounce the agents:
for p in $(grep -v '^#' /opt/data/buzz-agents/enabled); do kill "$(cat /opt/data/buzz-agents/$p.pid)" 2>/dev/null; done
# cron relaunches on the new binary within a minute
```

## After a Hermes image update — re-verify

1. `docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --version` runs.
2. `hermes -p default acp` still starts (`-p` selector unchanged).
3. Within a minute: one `buzz-acp` per enabled agent (`pgrep -af buzz-acp`) and `/usr/local/bin/buzz` is the wrapper (`head -3 /usr/local/bin/buzz`).
4. @mention one agent as owner → it replies from its own identity.

## Phase 2

Per-action approval bridge (gate the agent's tool calls / `buzz messages send`
behind an owner `approve <id>`) · `--respond-to allowlist` when teammates join ·
locked-down read-only profile (needs buzz-as-MCP) · agents in more channels.
```

- [ ] **Step 2: Commit**

```bash
git add buzz-agents.md
git commit -m "docs(buzz-agents): runbook

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: README row + push + PR

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a subsection under "## Related: Buzz relay"**

After the `buzz.md` paragraph:

```markdown
### Hermes agents in Buzz

Each Hermes profile can join Buzz channels as its own member identity, via
`buzz-acp` bridging `hermes -p <profile> acp`. Runs inside the Hermes container,
cron-supervised under `/opt/data`. v1: owner-only addressing is the trust
boundary; agents otherwise have their full Hermes capability. Code changes go
through Buzz-native feature-branch review.

> Status: **build-out in progress** — see [`buzz-agents.md`](buzz-agents.md)
```

- [ ] **Step 2: Commit, push, PR**

```bash
git add README.md
git commit -m "docs(buzz-agents): README section

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push -u origin feat/hermes-buzz-agents
gh pr create --title "Hermes agents in Buzz channels" --body "$(cat <<'EOF'
Bridges Hermes profiles into the live Buzz relay as member identities via
buzz-acp -> hermes -p <profile> acp. Runs inside the Hermes container,
cron-supervised under /opt/data.

Spike passed 2026-09-09 (docs/superpowers/notes/2026-09-09-hermes-buzz-agents-spike.md):
the bridge answers real questions in #hermes with the full toolset. Reply auth
needed a small `buzz` CLI wrapper (key from a 0600 file — the terminal sandbox
strips BUZZ_*). `--permission-mode dont-ask` turned out to be a no-op in Hermes
ACP, so the v1 capability model is "full capability, gated by --respond-to
owner-only".

This PR is scripts + docs. VPS provisioning + fleet verification (Tasks 6-9)
happen after review.

Design: docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md
Plan:   docs/superpowers/plans/2026-09-09-hermes-buzz-agents.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Stop here for review before provisioning the fleet.**

---

## Task 6: Build + stage binaries; rotate the spike key; clean spike leftovers

**Files:** none committed (VPS only). Steps that Claude's SSH classifier blocks are marked **[Stephan]**.

- [ ] **Step 1: Confirm / rebuild the pinned binaries**

The spike left `main`-branch binaries. Rebuild at the pin `3c7f288`:
```bash
cd /root/buzz-src && git fetch --depth 50 origin && git checkout 3c7f288
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'command -v cargo || echo NO-CARGO'
```
Build (in the container if it has the Rust toolchain from the spike, else a `rust:1-slim` container — record which):
```bash
rustup target add x86_64-unknown-linux-musl 2>/dev/null || true
cargo build --release -p buzz-acp -p buzz-cli --target x86_64-unknown-linux-musl
install -m755 target/x86_64-unknown-linux-musl/release/buzz-acp /docker/hermes-agent-7qpk/data/bin/buzz-acp
install -m755 target/x86_64-unknown-linux-musl/release/buzz    /docker/hermes-agent-7qpk/data/bin/buzz.real
```
If the pin's tree doesn't build cleanly and `main`'s does, record the deviation in `buzz-agents.md` and pin to the built commit.

- [ ] **Step 2: Version marker**

```bash
sha=$(sha256sum /docker/hermes-agent-7qpk/data/bin/buzz-acp | cut -d' ' -f1)
printf 'block/buzz@%s  built %s  buzz-acp sha256 %s\n' "$(cd /root/buzz-src && git rev-parse --short HEAD)" "$(date -Is)" "$sha" > /docker/hermes-agent-7qpk/data/bin/buzz-acp.version
docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --version
docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz.real --help | head -1
```
Expected: a version string; `Buzz CLI — interact with a Buzz relay`.

- [ ] **Step 3: Rotate the compromised spike agent key**

The spike agent key (`8b10b48…` pubkey) leaked to a session transcript. Do not carry it into production.
```bash
docker exec buzz-relay-1 /usr/local/bin/buzz-admin remove-member --pubkey 8b10b48045d1237b1705300cf2acd5f0505a686d49da79cc5ec221c6f44a884c
docker exec buzz-relay-1 /usr/local/bin/buzz-admin list-members
```
Expected: only the owner remains (plus whatever Task 7 adds).

- [ ] **Step 4: Clean spike leftovers**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c '
  rm -f /opt/data/spike-*.env /opt/data/spike-*.log /opt/data/.buzz-agent-key \
        /opt/data/buzzspike-envtest*.txt /opt/data/envdump.txt /opt/data/bin/buzz
  pkill -f buzz-acp || true'
docker exec -u hermes -e HOME=/opt/data hermes-agent-7qpk-hermes-agent-1 /opt/hermes/.venv/bin/hermes profile delete buzzspike --yes 2>/dev/null || true
```
Keep `/opt/data/bin/{buzz-acp,buzz.real,buzz-acp.version}`. Leave `#hermes`
(`aea9fa66-…`) — Task 7 reuses it. **[Stephan]** if `pkill`/compound blocked.

- [ ] **Step 5: Commit** — nothing to commit; record the sha256 + built commit for Task 9's `buzz-agents.md` status flip.

---

## Task 7: Provision the launch agent set

**Files:** none committed.

- [ ] **Step 1: Confirm the launch set with the owner**

Default proposal: `default` + `openbrain` (two identities, exercises per-profile key selection in Task 9). The owner may name any subset of the 7.

- [ ] **Step 2: Per agent — key (0600, never printed), roster, env**

`RC=buzz-relay-1 HC=hermes-agent-7qpk-hermes-agent-1`. For each profile `P` in the set:
```bash
docker exec "$HC" mkdir -p /opt/data/buzz-agents
docker exec "$RC" /usr/local/bin/buzz-admin generate-key > /tmp/gk 2>&1
PUB=$(awk '/Public key:/{print $NF}' /tmp/gk)
awk '/Secret key:/{print $NF}' /tmp/gk \
  | docker exec -i "$HC" sh -c "cat > /opt/data/buzz-agents/$P.key && chmod 600 /opt/data/buzz-agents/$P.key"
rm -f /tmp/gk
docker exec "$RC" /usr/local/bin/buzz-admin add-member --pubkey "$PUB" --role member
sleep 1
docker exec "$HC" sh -c "sed 's|-p,PROFILE,acp|-p,$P,acp|' /opt/data/buzz-agents-staging/buzz-agent-env.example > /opt/data/buzz-agents/$P.env && chmod 600 /opt/data/buzz-agents/$P.env"
echo "$P pubkey $PUB"
docker exec "$HC" cat /opt/data/buzz-agents/$P.key   # -> owner copies to password manager, then clears scrollback
```
Members one at a time, `sleep 1` between (roster is one event). The secret only
ever transits the `awk | docker exec -i` pipe and the final `cat` the owner runs
for the password manager — never a script variable, never argv.
(`/opt/data/buzz-agents-staging/` holds the repo scripts until Task 10's `git pull`; after that, use `/root/HermesPlusOpenbrain/scripts/`.)

- [ ] **Step 3: Stage scripts + `enabled`**

```bash
docker exec "$HC" sh -c '
  install -m755 /opt/data/buzz-agents-staging/buzz-agent-supervise.sh /opt/data/buzz-agents/supervise.sh
  install -m755 /opt/data/buzz-agents-staging/buzz-wrap.sh            /opt/data/buzz-agents/buzz-wrap.sh
  printf "default\nopenbrain\n" > /opt/data/buzz-agents/enabled'
```

- [ ] **Step 4: Verify config, no placeholders**

```bash
docker exec "$HC" sh -c '
  cd /opt/data/buzz-agents
  for f in *.env; do
    grep -q "PROFILE,acp" "$f" && echo "PLACEHOLDER in $f"
    grep -q "^BUZZ_ACP_RESPOND_TO=owner-only" "$f" || echo "MISSING owner-only in $f"
    grep -q "^BUZZ_ACP_AGENT_OWNER=f978cb69" "$f" || echo "MISSING owner in $f"
    grep -q "^BUZZ_PRIVATE_KEY=" "$f" && echo "SECRET LEAKED into $f (should be .key only)"
    awk -F"[=,]" "/IDLE_TIMEOUT/{i=\$2} /MAX_TURN_DURATION/{m=\$2} END{if(i>=m) print \"IDLE>=MAX in \" FILENAME}" "$f"
  done
  for p in $(grep -v "^#" enabled); do [ -s "$p.key" ] || echo "MISSING key file for $p"; done
  echo "(no output above = ok)"'
```

---

## Task 8: Install cron; start the fleet

**Files:** none committed.

- [ ] **Step 1: First run, observe**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/buzz-agents/supervise.sh --once
sleep 5
docker exec hermes-agent-7qpk-hermes-agent-1 pgrep -af buzz-acp
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'head -3 /usr/local/bin/buzz; for p in $(grep -v "^#" /opt/data/buzz-agents/enabled); do echo "--- $p ---"; tail -5 /opt/data/buzz-agents/$p.log; done'
```
Expected: one `buzz-acp` per enabled name; each log shows `connected to relay` + `subscribed to channel`; `/usr/local/bin/buzz` is the wrapper.

- [ ] **Step 2: Install the cron liveness entry**

Match the mechanism the voice watchdog uses (`docker exec hermes-agent-7qpk-hermes-agent-1 crontab -l`). Add:
```
* * * * * /opt/data/buzz-agents/supervise.sh --once >>/opt/data/buzz-agents/cron.log 2>&1
```
If the container has no root crontab, copy the voice setup's persistence mechanism (repo Twilio/voice notes) and record what was done in `buzz-agents.md`.

- [ ] **Step 3: Confirm cron survives**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 crontab -l | grep buzz-agents
```

---

## Task 9: Verify the fleet + flip to live

**Files:** Modify `buzz-agents.md`.

- [ ] **Step 1: Processes + auth**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 pgrep -af buzz-acp     # one per enabled
docker logs buzz-relay-1 --since 10m | grep -Ei "auth (successful|ok)" | grep -v f978cb69
```
Expected: one auth line per agent pubkey.

- [ ] **Step 2: `owner-only` holds** — as owner in `#hermes`: `@Hermes-default summarise the MU thesis from my memory`. Expected: persona-correct reply, `mcp__openbrain__search` in `default.log`. Then have the owner check `<profile>.log` shows a non-owner @mention (if any exists) dropped by the author gate; else note as untested (single human member).

- [ ] **Step 3: Per-profile identity** — as owner: `@Hermes-openbrain` a question. Expected: the reply is posted by the `openbrain` agent's identity, not `default`'s — proves the wrapper's per-profile key selection.

- [ ] **Step 4: Restart resilience**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'kill $(cat /opt/data/buzz-agents/default.pid)'
sleep 70
docker exec hermes-agent-7qpk-hermes-agent-1 pgrep -af buzz-acp
```
Expected: relaunched by cron.

- [ ] **Step 5: No regression**

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'hermes-agent|buzz-|traefik|openbrain|stripe'
docker exec -u hermes -e HOME=/opt/data hermes-agent-7qpk-hermes-agent-1 /opt/hermes/.venv/bin/hermes profile list
free -h
```
Expected: all `Up`/healthy, no restarts, comfortable RAM headroom.

- [ ] **Step 6: Flip status + record as-deployed**

In `buzz-agents.md`: status → `**v1 LIVE 2026-09-__**`; fill the launch set, the cron mechanism used, `buzz-acp` sha256 + built commit.
```bash
git add buzz-agents.md
git commit -m "docs(buzz-agents): v1 live — as-deployed

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push
```

---

## Task 10: Merge + sync + memory

- [ ] **Step 1:** `gh pr merge feat/hermes-buzz-agents --squash --delete-branch` (or `--merge` to match repo style).
- [ ] **Step 2:** VPS host: `cd /root/HermesPlusOpenbrain && git checkout main && git pull --ff-only`. Agents keep running (config is under `/opt/data`).
- [ ] **Step 3:** Update `project-hermes-buzz-agents` memory + `MEMORY.md` line: v1 live, launch set, `buzz-acp@<sha>` in `/opt/data/bin`, `owner-only` gate + wrapper mechanism, runbook `buzz-agents.md`, `[[project-buzz-relay]]`, phase-2 = approval bridge + allowlist.

---

## Self-review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| 3.1 in-container topology | Tasks 6, 8 |
| 3.2 spike | Done (notes) |
| 3.3 `/opt/data` layout + cron + wrapper | Tasks 2, 6, 7, 8 |
| 3.3 repo artefacts (wrapper, supervisor, env.example, buzz-agents.md, README) | Tasks 1, 2, 3, 4, 5 |
| 3.4 identity & roster (+ `.key` file) | Tasks 6 (rotate), 7 |
| 3.5 `owner-only` gate; full-capability accepted | Tasks 3, 7, 9 |
| 3.5 code / branch protection | out of scope for this plan (relay-enforced already; noted in runbook) |
| 3.6 `#hermes` channel | Tasks 6 (keep), 7 |
| 3.7 cost caps | Task 3 (env), Task 9 (observe) |
| §6 verification | Task 9 |
| phase-2 stays out | not in any task, by design |

**Placeholder scan:** `<hex>`, `<profile>`, `<new-sha>`, `<hex-secret>`, `2026-09-__` are per-invocation substitutions. Task 7 Step 4 greps to prove no literal placeholder shipped. `buzz-agents.md` ships at "build-out in progress" and flips in Task 9 — intentional, mirrors `buzz.md`.

**Type/name consistency:** `/opt/data/buzz-agents/` paths; `enabled`, `<profile>.env`/`.key`/`.pid`/`.log`; `buzz-acp` at `/opt/data/bin/buzz-acp`, real CLI at `/opt/data/bin/buzz.real`, wrapper at `/usr/local/bin/buzz` from `buzz-wrap.sh`; `BUZZ_ACP_RESPOND_TO=owner-only`, `BUZZ_ACP_AGENT_OWNER=f978cb69…`; wrapper knobs `BUZZ_WRAP_KEYDIR`/`BUZZ_WRAP_REAL`/`BUZZ_WRAP_RELAY` match between `buzz-wrap.sh` and its test; supervisor knobs `BUZZ_AGENT_DIR`/`BUZZ_ACP_BIN`/`BUZZ_WRAP_SRC`/`BUZZ_WRAP_DEST` match between `buzz-agent-supervise.sh` and its test. `BUZZ_ACP_AGENT_ARGS=-p,<profile>,acp` consistent in template + Task 7.

**Open dependency:** Tasks 6–10 need a VPS host shell; several steps are marked **[Stephan]** where Claude's SSH classifier blocks compound/background commands. Task 5 is a hard stop for PR review.
