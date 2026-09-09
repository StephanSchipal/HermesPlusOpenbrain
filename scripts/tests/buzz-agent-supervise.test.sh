#!/bin/sh
# supervise.sh --once: one buzz-acp per enabled name, wrapper installed, profile
# marker exported, relaunch after a kill, idempotent re-run.
set -eu
TMP=$(mktemp -d)
cleanup() {
  # Kill only the stub buzz-acp processes we spawned (recorded in *.pid),
  # never the whole process group.
  for pf in "$TMP"/agents/*.pid; do
    [ -f "$pf" ] || continue
    kill "$(cat "$pf" 2>/dev/null)" 2>/dev/null || true
  done
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir -p "$TMP/agents" "$TMP/localbin"
# stub buzz-acp: record profile marker + pid, then sleep
cat > "$TMP/agents/buzz-acp" <<'EOF'
#!/bin/sh
echo "started profile=$BUZZ_AGENT_PROFILE pid=$$" >> "$SUP_STARTED"
exec sleep 300
EOF
chmod +x "$TMP/agents/buzz-acp"
printf '#!/bin/sh\necho wrap\n' > "$TMP/agents/buzz-wrap.sh"
printf 'alpha\nbravo\n# comment\n\n' > "$TMP/agents/enabled"
for a in alpha bravo; do printf 'BUZZ_PRIVATE_KEY=x\n' > "$TMP/agents/$a.env"; done

export SUP_STARTED="$TMP/started"
: > "$SUP_STARTED"
run() {
  BUZZ_AGENT_DIR="$TMP/agents" BUZZ_ACP_BIN="$TMP/agents/buzz-acp" \
  BUZZ_WRAP_SRC="$TMP/agents/buzz-wrap.sh" BUZZ_WRAP_DEST="$TMP/localbin/buzz" \
    sh scripts/buzz-agent-supervise.sh --once
}

run; sleep 1
grep -q 'profile=alpha' "$SUP_STARTED" || { echo "FAIL: alpha not started"; exit 1; }
grep -q 'profile=bravo' "$SUP_STARTED" || { echo "FAIL: bravo not started"; exit 1; }
[ -x "$TMP/localbin/buzz" ] || { echo "FAIL: wrapper not installed"; exit 1; }
[ "$(grep -c started "$SUP_STARTED")" = 2 ] || { echo "FAIL: expected exactly 2 starts, got $(grep -c started "$SUP_STARTED")"; exit 1; }

# idempotent: second --once starts nothing new
run; sleep 1
[ "$(grep -c started "$SUP_STARTED")" = 2 ] || { echo "FAIL: not idempotent"; exit 1; }

# kill alpha, re-run, assert relaunch
kill "$(awk -F'pid=' '/profile=alpha/{print $2}' "$SUP_STARTED" | head -1)" 2>/dev/null || true
sleep 1
run; sleep 1
[ "$(grep -c 'profile=alpha' "$SUP_STARTED")" -ge 2 ] || { echo "FAIL: alpha not relaunched"; exit 1; }
[ "$(grep -c 'profile=bravo' "$SUP_STARTED")" = 1 ] || { echo "FAIL: bravo restarted unnecessarily"; exit 1; }

echo "PASS"
