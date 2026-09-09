# Hermes agents in Buzz — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run each chosen Hermes profile as a member of the Buzz relay, so people and Hermes agents coordinate in the same channels — via `buzz-acp` bridging `hermes acp` (ACP/stdio) into Buzz.

**Architecture:** One `buzz-acp` process per agent, each with its own Nostr keypair on the relay roster, spawning `hermes acp` for the agent's profile. Runs **inside the live `hermes-agent-7qpk` container**, cron/watchdog-supervised, with all state under `/opt/data` so it survives Hermes image updates. v1 capability model: `--permission-mode dont-ask` + `--respond-to owner-only`; code review uses Buzz-native feature-branch channels + relay-enforced signed-merge approval.

**Tech Stack:** Rust (`buzz-acp`, built from `block/buzz`), Hermes ACP, Buzz relay (live), POSIX sh (`supervise.sh`), cron.

**Design doc:** [`docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md`](../specs/2026-09-09-hermes-buzz-agents-design.md)
**Relay it builds on:** [`buzz.md`](../../../buzz.md) (Buzz relay, live since 2026-09-09)

**Upstream `buzz-acp`:** `github.com/block/buzz`, `crates/buzz-acp`, pinned to `block/buzz` commit `3c7f288` (same commit the relay image is pinned to). Not published as a binary — built from source.

---

## Pre-flight facts (verified 2026-09-09)

- Hermes container: `hermes-agent-7qpk-hermes-agent-1`, `$HERMES_HOME=/opt/data`, host path `/docker/hermes-agent-7qpk/data`. Profiles: `default`, `coder`, `designer`, `master`, `openbrain`, `researcher`, `writer`. `hermes acp` = ACP server over stdio.
- Relay: `wss://buzz.srv1608402.hstgr.cloud`, container `buzz-relay-1`, `buzz-admin` has `generate-key` / `add-member` / `list-members`.
- Owner pubkey (hex): `f978cb6960678ad53d9c6ea9d9e0fc9510c494e67041d36a5e9d2da719556aa6`.
- `buzz-acp` defaults that MUST be overridden: `--permission-mode` defaults to `bypass-permissions` (autonomous) → set `dont-ask`. `--respond-to` defaults to `owner-only` → keep, and set `BUZZ_ACP_AGENT_OWNER` to the owner hex or the gate drops everything.
- Reaching the VPS host shell: Stephan's SSH key lands in the hermes container; use hPanel → Browser terminal for a host root shell. Claude's SSH reaches the host directly.

---

## File structure

| File | Responsibility | Action |
| --- | --- | --- |
| `buzz-agents.md` | Runbook + spike findings + file layout + add/remove agent + rebuild + image-update re-verify | Create (Task 1, expanded Task 5) |
| `scripts/buzz-agent-supervise.sh` | Supervisor: one `buzz-acp` per name in `enabled`, restart-on-exit, per-agent log | Create (Task 3) |
| `scripts/buzz-agent-env.example` | Per-agent env template with every var documented | Create (Task 4) |
| `README.md` | One row under the Buzz section → `buzz-agents.md` | Modify (Task 6) |
| VPS `/opt/data/bin/buzz-acp` | The built binary (not committed) | Task 2, 9 |
| VPS `/opt/data/buzz-agents/{enabled,*.env,supervise.sh}` | Runtime config (not committed) | Task 8, 9 |
| VPS crontab | Liveness for `supervise.sh` | Task 9 |

No compose file — this runs inside a vendor-managed container.

---

## Task 1: Spike — one Hermes agent answering in Buzz, by hand

**This task gates the plan.** If it can't be made to work, STOP and report — the sidecar topology or an upstream fix becomes the plan.

**Files:**
- Create: `buzz-agents.md` (spike-findings skeleton only; expanded in Task 5)

- [ ] **Step 1: Build `buzz-acp` (musl-static) on the VPS host**

Run on the VPS host:
```bash
mkdir -p /root/buzz-acp-build && cd /root/buzz-acp-build
docker run --rm -v "$PWD":/out messense/rust-musl-cross:x86_64-musl bash -c '
  set -e
  git clone --depth 1 --branch main https://github.com/block/buzz /tmp/buzz
  cd /tmp/buzz && git fetch --depth 1 origin 3c7f288 && git checkout 3c7f288
  cargo build --release -p buzz-acp --target x86_64-unknown-linux-musl
  cp target/x86_64-unknown-linux-musl/release/buzz-acp /out/buzz-acp
'
file /root/buzz-acp-build/buzz-acp
```
Expected: `buzz-acp: ELF 64-bit LSB executable, x86-64, statically linked`. If the musl build fails on a C dependency, retry with `rust:1-slim-bookworm` + a plain `cargo build --release -p buzz-acp` (glibc) — the Hermes container is Debian-based so a glibc binary is acceptable; record which was used.

- [ ] **Step 2: Copy the binary into the Hermes container's durable volume**

```bash
mkdir -p /docker/hermes-agent-7qpk/data/bin
cp /root/buzz-acp-build/buzz-acp /docker/hermes-agent-7qpk/data/bin/buzz-acp
chmod 755 /docker/hermes-agent-7qpk/data/bin/buzz-acp
docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --help | head -5
```
Expected: `buzz-acp` help text (proves it runs in the container).

- [ ] **Step 3: Mint one agent keypair and register it**

```bash
docker exec buzz-relay-1 /usr/local/bin/buzz-admin generate-key
```
Records a hex pubkey + secret. Save the secret to the password manager now ("Buzz agent — hermes/default (spike)"). Then:
```bash
cd /root/HermesPlusOpenbrain/deploy
docker compose -f docker-compose.buzz.yml exec relay \
  /usr/local/bin/buzz-admin add-member --pubkey <agent-hex-pubkey> --role member
docker compose -f docker-compose.buzz.yml exec relay /usr/local/bin/buzz-admin list-members
```
Expected: two members (owner + this agent).

- [ ] **Step 4: Determine how `hermes acp` selects a profile**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'hermes --help 2>&1 | grep -iE "profile|-p "'
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'ls -la $(command -v coder designer researcher writer master 2>/dev/null)'
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'cat $(command -v coder) 2>/dev/null'
```
Establish the selector: a top-level `hermes --profile X acp` flag, a `HERMES_PROFILE=X` env var, or the alias wrapper (`coder acp`). **Record the exact working form in `buzz-agents.md`.**

- [ ] **Step 5: Run the bridge by hand for `default`**

```bash
docker exec -e BUZZ_PRIVATE_KEY='<agent-nsec-or-hex-secret>' \
  -e BUZZ_RELAY_URL='wss://buzz.srv1608402.hstgr.cloud' \
  -e BUZZ_ACP_AGENT_COMMAND='hermes' \
  -e BUZZ_ACP_AGENT_ARGS='acp' \
  -e BUZZ_ACP_PERMISSION_MODE='dont-ask' \
  -e BUZZ_ACP_RESPOND_TO='owner-only' \
  -e BUZZ_ACP_AGENT_OWNER='f978cb6960678ad53d9c6ea9d9e0fc9510c494e67041d36a5e9d2da719556aa6' \
  -e BUZZ_ACP_AGENTS='1' \
  hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp
```
(Adjust `BUZZ_ACP_AGENT_ARGS` per Step 4 — e.g. `acp` with `HERMES_PROFILE` also `-e`, or a comma form like `--profile,default,acp`.)
Watch the output. Expected: connects, `NIP-42 auth` succeeds, "listening" / channel discovery. If it 401s on the HTTP bridge, add `-e BUZZ_API_TOKEN=...` — **and record where that token comes from** (check `buzz-admin` help, the relay `/api` docs, or the `api_tokens` table).

- [ ] **Step 6: Create `#hermes`, add the agent, @mention it**

In the Buzz **desktop app** (as owner): create channel `hermes`; add the agent (member management, or `buzz messages`/`buzz channels` CLI). Post `@Hermes … what's in my OpenBrain memory about Micron?`.
Expected: the agent replies in-channel, in its persona, having actually called an OpenBrain read tool. Check the hand-run `buzz-acp` output and `docker logs buzz-relay-1` for the turn.

- [ ] **Step 7: Confirm `dont-ask` holds**

As owner, post `@Hermes create a file /tmp/xyz with "hi"`.
Expected: the agent declines / says it can't, and nothing is written (`docker exec hermes-agent-7qpk-hermes-agent-1 ls /tmp/xyz` → not found). If Hermes instead blocks on a TTY prompt or the write succeeds, **STOP** — `dont-ask` isn't effective; report for a design revision.

- [ ] **Step 8: Record findings and commit the skeleton**

Create `buzz-agents.md`:
```markdown
# Hermes agents in Buzz

**Status: SPIKE PASSED 2026-09-09 — build-out in progress.**

Bridges Hermes profiles into the Buzz relay ([`buzz.md`](buzz.md)) as member
identities, via `buzz-acp` → `hermes acp`. Design:
`docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md`.

## Spike findings (2026-09-09)

- `buzz-acp` build: <musl | glibc>, from `block/buzz@3c7f288`, `cargo build --release -p buzz-acp`.
- Profile selector for `hermes acp`: `<exact form, e.g. HERMES_PROFILE=coder hermes acp>`.
- `BUZZ_ACP_PERMISSION_MODE=dont-ask`: <honoured — agent declined a write | notes>.
- `--respond-to owner-only` + `BUZZ_ACP_AGENT_OWNER=<hex>`: <behaviour observed>.
- Relay auth: <member-key NIP-42 sufficient | BUZZ_API_TOKEN required, minted via ___>.
- Networking: hand-run used `wss://buzz.srv1608402.hstgr.cloud` from the Hermes container — <worked | used ws://… instead>.
- Cost: ~<N> tokens for one real turn; idle = 0.
- Anything surprising: <…>

(Runbook sections added in Task 5.)
```
Fill every `<…>` from the spike. Then:
```bash
git checkout -b feat/hermes-buzz-agents   # if not already on it
git add buzz-agents.md
git commit -m "docs(buzz-agents): spike findings — one Hermes agent live in Buzz

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 9: Tear down the spike**

`Ctrl-C` the hand-run `buzz-acp`. Leave the agent keypair + membership + `#hermes` channel in place (Task 7 reuses them). Remove `/root/buzz-acp-build` clone if disk-tight (keep the `buzz-acp` binary).

**Report:** DONE with the filled findings, or BLOCKED with which step failed.

---

## Task 2: Stage the `buzz-acp` binary properly

**Files:** none committed (VPS only)

- [ ] **Step 1: Put the binary at its runtime path with a version marker**

```bash
install -m755 /root/buzz-acp-build/buzz-acp /docker/hermes-agent-7qpk/data/bin/buzz-acp
echo "block/buzz@3c7f288  built $(date -Is)  $(sha256sum /docker/hermes-agent-7qpk/data/bin/buzz-acp | cut -d' ' -f1)" \
  > /docker/hermes-agent-7qpk/data/bin/buzz-acp.version
docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --version
```
Expected: a version string; `.version` file recorded (Task 5 references it).

- [ ] **Step 2: Commit** — nothing to commit; note the sha256 in the task log for Task 5.

---

## Task 3: The supervisor script

**Files:**
- Create: `scripts/buzz-agent-supervise.sh`
- Test: `scripts/tests/buzz-agent-supervise.test.sh`

- [ ] **Step 1: Write the failing test**

`scripts/tests/buzz-agent-supervise.test.sh`:
```sh
#!/bin/sh
# Runs supervise.sh against a stub buzz-acp; asserts it launches one per enabled
# name and relaunches after a kill.
set -eu
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"; kill 0 2>/dev/null || true' EXIT

mkdir -p "$TMP/agents"
cat > "$TMP/agents/buzz-acp" <<'EOF'
#!/bin/sh
echo "stub $BUZZ_ACP_PROFILE $$" >> "$LOGDIR/started"
exec sleep 300
EOF
chmod +x "$TMP/agents/buzz-acp"
printf 'alpha\nbravo\n' > "$TMP/agents/enabled"
for a in alpha bravo; do
  printf 'BUZZ_ACP_PROFILE=%s\nBUZZ_PRIVATE_KEY=x\n' "$a" > "$TMP/agents/$a.env"
done

export LOGDIR="$TMP/logs"; mkdir -p "$LOGDIR"
BUZZ_AGENT_DIR="$TMP/agents" BUZZ_ACP_BIN="$TMP/agents/buzz-acp" \
  sh scripts/buzz-agent-supervise.sh --once
sleep 1
grep -q "stub alpha" "$LOGDIR/started" || { echo "FAIL: alpha not started"; exit 1; }
grep -q "stub bravo" "$LOGDIR/started" || { echo "FAIL: bravo not started"; exit 1; }

# kill alpha, re-run, assert relaunch
pkill -f "BUZZ_ACP_PROFILE=alpha" 2>/dev/null || kill "$(head -1 "$TMP/agents/alpha.pid")" 2>/dev/null || true
sleep 1
BUZZ_AGENT_DIR="$TMP/agents" BUZZ_ACP_BIN="$TMP/agents/buzz-acp" \
  sh scripts/buzz-agent-supervise.sh --once
sleep 1
[ "$(grep -c 'stub alpha' "$LOGDIR/started")" -ge 2 ] || { echo "FAIL: alpha not relaunched"; exit 1; }
echo "PASS"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `sh scripts/tests/buzz-agent-supervise.test.sh`
Expected: FAIL — `scripts/buzz-agent-supervise.sh` does not exist.

- [ ] **Step 3: Write `scripts/buzz-agent-supervise.sh`**

```sh
#!/bin/sh
# One buzz-acp per name in $BUZZ_AGENT_DIR/enabled. Idempotent: starts only what
# is not already running; with no flag, loops forever re-checking every 15s.
# Deployed to /opt/data/buzz-agents/supervise.sh, kept alive by cron.
set -eu

BUZZ_AGENT_DIR="${BUZZ_AGENT_DIR:-/opt/data/buzz-agents}"
BUZZ_ACP_BIN="${BUZZ_ACP_BIN:-/opt/data/bin/buzz-acp}"
LOGDIR="${LOGDIR:-$BUZZ_AGENT_DIR}"
LOCK="$BUZZ_AGENT_DIR/.supervise.lock"

# single supervisor
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then flock -n 9 || exit 0; fi

start_one() {
  name=$1
  pidf="$BUZZ_AGENT_DIR/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then return 0; fi
  [ -f "$BUZZ_AGENT_DIR/$name.env" ] || { echo "$(date -Is) no env for $name" >&2; return 0; }
  # shellcheck disable=SC1090
  ( set -a; . "$BUZZ_AGENT_DIR/$name.env"; set +a
    exec "$BUZZ_ACP_BIN" >>"$LOGDIR/$name.log" 2>&1 ) &
  echo $! > "$pidf"
  echo "$(date -Is) started $name pid $!" >>"$LOGDIR/supervise.log"
}

sweep() {
  [ -f "$BUZZ_AGENT_DIR/enabled" ] || return 0
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    case "$name" in \#*) continue;; esac
    start_one "$name"
  done < "$BUZZ_AGENT_DIR/enabled"
}

sweep
[ "${1:-}" = "--once" ] && exit 0
while :; do sleep 15; sweep; done
```

- [ ] **Step 4: Run the test, verify it passes**

Run: `sh scripts/tests/buzz-agent-supervise.test.sh`
Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add scripts/buzz-agent-supervise.sh scripts/tests/buzz-agent-supervise.test.sh
git commit -m "feat(buzz-agents): supervisor script (one buzz-acp per enabled agent)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: The per-agent env template

**Files:**
- Create: `scripts/buzz-agent-env.example`

- [ ] **Step 1: Write it** (fill the profile-selector line from `buzz-agents.md` § Spike findings)

```sh
# Copy to /opt/data/buzz-agents/<profile>.env on the VPS, chmod 600. One per agent.
# Never commit a filled copy — BUZZ_PRIVATE_KEY is a secret.

# --- identity ---
BUZZ_PRIVATE_KEY=nsec1_or_hex_secret_from_buzz-admin_generate-key
BUZZ_RELAY_URL=wss://buzz.srv1608402.hstgr.cloud

# --- which Hermes profile this agent is ---
BUZZ_ACP_AGENT_COMMAND=hermes
# Profile selector — EXACT form confirmed in the spike (buzz-agents.md). One of:
#   BUZZ_ACP_AGENT_ARGS=acp   plus   HERMES_PROFILE=coder
#   BUZZ_ACP_AGENT_ARGS=--profile,coder,acp
BUZZ_ACP_AGENT_ARGS=acp
# HERMES_PROFILE=coder

# --- capability gates (v1) — DO NOT loosen without the design spec ---
BUZZ_ACP_PERMISSION_MODE=dont-ask
BUZZ_ACP_RESPOND_TO=owner-only
BUZZ_ACP_AGENT_OWNER=f978cb6960678ad53d9c6ea9d9e0fc9510c494e67041d36a5e9d2da719556aa6
BUZZ_ACP_AGENTS=1

# --- turn caps ---
BUZZ_ACP_IDLE_TIMEOUT=620
BUZZ_ACP_MAX_TURN_DURATION=7200

# --- only if the spike showed the HTTP bridge needs it ---
# BUZZ_API_TOKEN=
```

- [ ] **Step 2: Commit**

```bash
git add scripts/buzz-agent-env.example
git commit -m "feat(buzz-agents): per-agent env template

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Expand `buzz-agents.md` into the runbook

**Files:**
- Modify: `buzz-agents.md`

- [ ] **Step 1: Append the runbook sections** (keep the spike-findings section)

Add, after the findings:

```markdown
## What runs

- `buzz-acp` binary: `/opt/data/bin/buzz-acp` (+ `.version` — commit, build date, sha256).
- Per-agent config: `/opt/data/buzz-agents/<profile>.env` (chmod 600; `BUZZ_PRIVATE_KEY` is a secret, also in the password manager).
- `/opt/data/buzz-agents/enabled` — newline list of profile names to run. **This is the "launch N of 7" knob.**
- `/opt/data/buzz-agents/supervise.sh` — copy of `scripts/buzz-agent-supervise.sh`; one `buzz-acp` per enabled name, relaunched on exit.
- cron: `* * * * * /opt/data/buzz-agents/supervise.sh --once >/dev/null 2>&1` (same liveness pattern as the voice gateway).
- Logs: `/opt/data/buzz-agents/<profile>.log`, `supervise.log`.

All under `/opt/data`, so a Hermes image update cannot remove it.

## Capability model (v1)

`--permission-mode dont-ask` — agents read/search/reason/draft/discuss, cannot
run shell / edit files / write memory. `--respond-to owner-only` — only the
owner (`f978cb69…`) can instruct an agent; others see replies only. Code: an
agent may push a **feature branch** (→ review channel); protected branches need
the owner's signed merge approval (relay-enforced). Stripe MCP is read-only.

## Add an agent

1. `docker exec buzz-relay-1 /usr/local/bin/buzz-admin generate-key` → save the secret to the password manager.
2. `docker compose -f /root/HermesPlusOpenbrain/deploy/docker-compose.buzz.yml exec relay /usr/local/bin/buzz-admin add-member --pubkey <hex> --role member`
3. `cp scripts/buzz-agent-env.example /opt/data/buzz-agents/<profile>.env`, fill it, `chmod 600`.
4. Add `<profile>` to `/opt/data/buzz-agents/enabled`.
5. `/opt/data/buzz-agents/supervise.sh --once` — starts it within a minute anyway via cron.
6. In the desktop app, add the agent to the channels it should see.

## Remove / pause an agent

Delete its line from `enabled`; `kill $(cat /opt/data/buzz-agents/<profile>.pid)`.
Full removal: also `buzz-admin remove-member --pubkey <hex>`.

## Owner controls (in-channel, as owner)

`!cancel` / `!rotate` / `!shutdown` — kind:9 message, exact body, agent mentioned via a separate p-tag.

## Rebuild buzz-acp (new upstream commit)

```bash
cd /root/buzz-acp-build && docker run --rm -v "$PWD":/out messense/rust-musl-cross:x86_64-musl bash -c '
  git clone --depth 1 https://github.com/block/buzz /tmp/b && cd /tmp/b && git checkout <new-sha>
  cargo build --release -p buzz-acp --target x86_64-unknown-linux-musl
  cp target/x86_64-unknown-linux-musl/release/buzz-acp /out/buzz-acp'
install -m755 buzz-acp /opt/data/bin/buzz-acp   # update .version
for p in $(cat /opt/data/buzz-agents/enabled); do kill "$(cat /opt/data/buzz-agents/$p.pid)"; done
# cron relaunches on the new binary
```

## After a Hermes image update — re-verify

1. `docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --version` still runs.
2. `hermes acp` still starts, profile selector unchanged (re-check `buzz-agents.md` § Spike findings).
3. Within a minute, cron has one `buzz-acp` per enabled agent (`pgrep -af buzz-acp`).
4. @mention one agent → it replies.

## Phase 2

Per-action approval bridge (buzz-acp change) · `--respond-to allowlist` when teammates join · agents in more channels · sidecar-container topology.
```

- [ ] **Step 2: Commit**

```bash
git add buzz-agents.md
git commit -m "docs(buzz-agents): full runbook

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: README row + push + PR

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a row under the "Related: Buzz relay" section**

After the `buzz.md` link paragraph, add:

```markdown
### Hermes agents in Buzz

Each Hermes profile can join Buzz channels as its own member identity, via
`buzz-acp` bridging `hermes acp`. Runs inside the Hermes container,
cron-supervised under `/opt/data`. v1: read/advise only (`dont-ask`), owner-only
addressing, Buzz-native git review for code.

> Status: **v1** — see [`buzz-agents.md`](buzz-agents.md)
```

- [ ] **Step 2: Commit, push, PR**

```bash
git add README.md
git commit -m "docs(buzz-agents): README section

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push -u origin feat/hermes-buzz-agents
gh pr create --title "Hermes agents in Buzz channels" --body "$(cat <<'EOF'
Bridges Hermes profiles into the live Buzz relay as member identities via
buzz-acp -> hermes acp. Runs inside the Hermes container, cron-supervised under
/opt/data. v1: dont-ask + owner-only + Buzz-native git review.

Spike (Task 1) passed: see buzz-agents.md § Spike findings.

Design: docs/superpowers/specs/2026-09-09-hermes-buzz-agents-design.md
Plan:   docs/superpowers/plans/2026-09-09-hermes-buzz-agents.md

Deploy of the launch agent set (Tasks 7-11) happens after review.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
**Stop here for review before deploying the agent fleet.**

---

## Task 7: Provision the launch agent set on the VPS

**Files:** none committed

- [ ] **Step 1: Decide the launch set** — default: just `default`. Confirm with the owner; more can be added later by repeating this task.

- [ ] **Step 2: Per agent — key, roster, env file**

For each profile in the launch set (the spike already did `default` — reuse that keypair if kept, else mint fresh):
```bash
docker exec buzz-relay-1 /usr/local/bin/buzz-admin generate-key         # save secret -> password manager
cd /root/HermesPlusOpenbrain/deploy
docker compose -f docker-compose.buzz.yml exec relay \
  /usr/local/bin/buzz-admin add-member --pubkey <hex> --role member
sleep 1
mkdir -p /opt/data/buzz-agents
cp /root/HermesPlusOpenbrain/scripts/buzz-agent-env.example /opt/data/buzz-agents/<profile>.env
# edit: BUZZ_PRIVATE_KEY, profile selector line (per spike)
chmod 600 /opt/data/buzz-agents/<profile>.env
```

- [ ] **Step 3: Write `enabled`**

```bash
printf 'default\n' > /opt/data/buzz-agents/enabled     # one name per launched profile
```

- [ ] **Step 4: Verify config, no placeholders**

```bash
for f in /opt/data/buzz-agents/*.env; do
  grep -q 'generate-key\|_or_hex_\|CHANGE' "$f" && echo "PLACEHOLDER in $f" || echo "$f ok"
  grep -q '^BUZZ_ACP_PERMISSION_MODE=dont-ask' "$f" || echo "MISSING dont-ask in $f"
  grep -q "^BUZZ_ACP_AGENT_OWNER=f978cb69" "$f" || echo "MISSING owner in $f"
done
```
Expected: every file `ok`, no missing lines.

---

## Task 8: Install the supervisor + cron; start

**Files:** none committed

- [ ] **Step 1: Place the supervisor**

```bash
cp /root/HermesPlusOpenbrain/scripts/buzz-agent-supervise.sh /opt/data/buzz-agents/supervise.sh
chmod 755 /opt/data/buzz-agents/supervise.sh
```

- [ ] **Step 2: Confirm the binary + version marker are in place** (from Task 2)

```bash
cat /opt/data/bin/buzz-acp.version
docker exec hermes-agent-7qpk-hermes-agent-1 /opt/data/bin/buzz-acp --version
```

- [ ] **Step 3: First run, foreground-ish**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c '/opt/data/buzz-agents/supervise.sh --once'
sleep 3
docker exec hermes-agent-7qpk-hermes-agent-1 pgrep -af buzz-acp
docker exec hermes-agent-7qpk-hermes-agent-1 tail -n 20 /opt/data/buzz-agents/default.log
```
Expected: one `buzz-acp` per enabled name; log shows relay connect + `NIP-42 auth`.

- [ ] **Step 4: Install the cron liveness entry (inside the container's crontab)**

Determine how the voice watchdog's cron is installed (`docker exec … crontab -l`) and add, matching that mechanism:
```
* * * * * /opt/data/buzz-agents/supervise.sh --once >>/opt/data/buzz-agents/cron.log 2>&1
```
If the container has no crontab for root, the voice setup's persistence mechanism (documented in the repo's Twilio/voice notes) is the model to copy — record what was actually done in `buzz-agents.md`.

---

## Task 9: Verify the fleet

**Files:** none

- [ ] **Step 1: Processes + auth**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 pgrep -af buzz-acp        # one per enabled
docker logs buzz-relay-1 --since 5m | grep -E "NIP-42 auth successful" | grep -v f978cb69
```
Expected: one auth line per agent pubkey.

- [ ] **Step 2: @mention → reply**

As owner in `#hermes`: `@Hermes summarise the MU thesis from my memory`.
Expected: persona-correct reply, OpenBrain read tool used (check `default.log`).

- [ ] **Step 3: `dont-ask` holds**

As owner: `@Hermes write "test" to /tmp/should-not-exist`.
Expected: agent declines; `docker exec hermes-agent-7qpk-hermes-agent-1 ls /tmp/should-not-exist` → not found.

- [ ] **Step 4: `owner-only` holds** (if a second member exists; else note as untested)

From a non-owner identity, @mention an agent → no response; `default.log` shows the event dropped by the author gate.

- [ ] **Step 5: Restart resilience**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 sh -c 'kill $(cat /opt/data/buzz-agents/default.pid)'
sleep 70   # cron interval
docker exec hermes-agent-7qpk-hermes-agent-1 pgrep -af buzz-acp
```
Expected: relaunched.

---

## Task 10: Code-review path (feature branch → review channel)

**Files:** none — may slip to a follow-up if Buzz Projects setup is non-trivial; record the outcome.

- [ ] **Step 1: Create a throwaway repo in Buzz** with branch protection on `main` (`buzz-protect main require-approval 1`, owner in `push-allowed`).
- [ ] **Step 2:** As owner, `@Hermes on a branch, add a README line and push`.
- [ ] **Step 3:** Expected: agent pushes `hermes/*` branch → a review channel with the diff appears. A direct push to `main` is refused by the relay without a signed approval.
- [ ] **Step 4:** Record in `buzz-agents.md` whether this worked end-to-end or is deferred.

---

## Task 11: Regression + flip to live

**Files:**
- Modify: `buzz-agents.md`

- [ ] **Step 1: No regression**

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'hermes-agent|buzz-|traefik|openbrain|stripe'
# voice / whatsapp / CLI still work: exercise one (e.g. hermes -p openbrain status)
docker exec hermes-agent-7qpk-hermes-agent-1 hermes profile list
```
Expected: everything `Up`/healthy, no restarts; gateways unaffected.

- [ ] **Step 2: Resources**

```bash
free -h && docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' | grep -E 'hermes|buzz'
```
Expected: comfortable headroom.

- [ ] **Step 3: Flip `buzz-agents.md` status + record as-deployed**

Status → `**v1 LIVE 2026-09-__**`; fill the launch set, the cron mechanism used, Task 10 outcome, `buzz-acp` sha256.

```bash
git add buzz-agents.md
git commit -m "docs(buzz-agents): v1 live — as-deployed

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git push
```

---

## Task 12: Merge + sync + memory

**Files:** none

- [ ] **Step 1:** `gh pr merge feat/hermes-buzz-agents --squash --delete-branch` (or `--merge` to match repo style).
- [ ] **Step 2:** VPS host: `cd /root/HermesPlusOpenbrain && git checkout main && git pull --ff-only`. Agents keep running (config is under `/opt/data`, not in the repo tree).
- [ ] **Step 3:** Update memory — new `project` memory `project-hermes-buzz-agents.md` + `MEMORY.md` line: v1 live, which profiles, `buzz-acp@<sha>` in `/opt/data/bin`, `dont-ask` + `owner-only`, runbook `buzz-agents.md`, `[[project-buzz-relay]]`, phase 2 = approval bridge + allowlist.

---

## Self-review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| 3.1 in-container topology | Tasks 1, 8 |
| 3.2 spike + all its unknowns | Task 1 (steps 4–7) |
| 3.3 `/opt/data` layout + cron | Tasks 2, 3, 7, 8 |
| 3.3 repo artefacts (supervise.sh, env.example, buzz-agents.md, README) | Tasks 3, 4, 5, 6 |
| 3.4 identity & roster | Tasks 1, 7 |
| 3.5 `dont-ask` + `owner-only` | Tasks 4, 7, 9 |
| 3.5 code / branch protection | Task 10 |
| 3.6 `#hermes` channel | Tasks 1, 7 |
| 3.7 cost caps | Task 4 (env), Task 11 (observe) |
| §6 verification | Tasks 9, 10, 11 |
| phase-2 stays out | not in any task, by design |

**Placeholder scan:** `<agent-hex-pubkey>`, `<new-sha>`, `<profile>` are per-invocation substitutions, not gaps. The profile-selector line in Tasks 4/7 explicitly defers to "the form confirmed in Task 1 § Spike findings" — a real produced artefact, and Task 7 Step 4 greps to prove no literal placeholder shipped. `buzz-agents.md` ships at `SPIKE PASSED` and flips in Task 11 — intentional, mirrors `buzz.md` / `stripe-mcp/DEPLOY.md`.

**Type/name consistency:** `/opt/data/buzz-agents/` paths, `enabled` file, `<profile>.env` / `.pid` / `.log`, `BUZZ_ACP_PERMISSION_MODE=dont-ask`, `BUZZ_ACP_RESPOND_TO=owner-only`, `BUZZ_ACP_AGENT_OWNER=f978cb69…`, binary at `/opt/data/bin/buzz-acp` — identical across Tasks 1–11 and both scripts. Supervisor env knobs (`BUZZ_AGENT_DIR`, `BUZZ_ACP_BIN`, `LOGDIR`) match between the script and its test.

**Open dependency:** Tasks 2–12 assume Task 1 reports DONE. If Task 1 is BLOCKED, the plan pauses for a design revision (sidecar topology or upstream fix).

---

## Execution handoff

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between. Note Task 1 is a spike that must be evaluated before proceeding; Task 6 is a hard stop for PR review; Tasks 7–11 need a VPS host shell and Task 9/10 need the owner to post in Buzz.

**2. Inline Execution** — this session, checkpoints after Task 1 (spike verdict), Task 6 (PR), Task 9 (fleet verify).

Which approach?
