#!/bin/sh
# One buzz-acp per name in $BUZZ_AGENT_DIR/enabled. Idempotent: starts only what
# is not already running. Also (re)installs the buzz reply wrapper — /usr/local/bin
# is not a Docker volume, so it is lost on container recreate. With no flag,
# loops forever re-checking every 15s.
#
# Deployed to /opt/data/buzz-agents/supervise.sh, kept alive by cron
# (`supervise.sh --once` every minute).
#
# Test knobs: BUZZ_AGENT_DIR, BUZZ_ACP_BIN, BUZZ_WRAP_SRC, BUZZ_WRAP_DEST, LOGDIR.
set -eu

BUZZ_AGENT_DIR="${BUZZ_AGENT_DIR:-/opt/data/buzz-agents}"
BUZZ_ACP_BIN="${BUZZ_ACP_BIN:-/opt/data/bin/buzz-acp}"
BUZZ_WRAP_SRC="${BUZZ_WRAP_SRC:-$BUZZ_AGENT_DIR/buzz-wrap.sh}"
BUZZ_WRAP_DEST="${BUZZ_WRAP_DEST:-/usr/local/bin/buzz}"
LOGDIR="${LOGDIR:-$BUZZ_AGENT_DIR}"
LOCK="$BUZZ_AGENT_DIR/.supervise.lock"

mkdir -p "$BUZZ_AGENT_DIR" "$LOGDIR"

# One supervisor at a time. NOTE: children spawned below MUST close fd 9
# (`exec 9>&-`) or an orphaned buzz-acp keeps the flock held and every later
# sweep silently no-ops.
exec 9>"$LOCK"
if command -v flock >/dev/null 2>&1; then flock -n 9 || exit 0; fi

log() { echo "$(date -Is) $*" >>"$LOGDIR/supervise.log"; }

install_wrapper() {
  # Best-effort. The host watchdog (buzz-agents-watchdog.sh) installs the
  # wrapper as root; this is only a fallback for a standalone/root run. Silent
  # when the destination dir isn't writable (the normal case, run as hermes).
  [ -r "$BUZZ_WRAP_SRC" ] || return 0
  [ -w "$(dirname "$BUZZ_WRAP_DEST")" ] || return 0
  if ! cmp -s "$BUZZ_WRAP_SRC" "$BUZZ_WRAP_DEST" 2>/dev/null; then
    install -m 0755 "$BUZZ_WRAP_SRC" "$BUZZ_WRAP_DEST" 2>/dev/null \
      && log "installed wrapper -> $BUZZ_WRAP_DEST"
  fi
}

start_one() {
  name=$1
  pidf="$BUZZ_AGENT_DIR/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null; then
    return 0
  fi
  if [ ! -f "$BUZZ_AGENT_DIR/$name.env" ]; then
    log "no env for $name — skipping"
    return 0
  fi
  if [ ! -s "$BUZZ_AGENT_DIR/$name.key" ]; then
    log "no key for $name — skipping"
    return 0
  fi
  # shellcheck disable=SC1090
  ( set -a; . "$BUZZ_AGENT_DIR/$name.env"; set +a
    # The secret lives only in <name>.key (0600). buzz-acp reads it as
    # BUZZ_PRIVATE_KEY; keeping it out of <name>.env means the env file is safe
    # to read/diff and the key has a single source of truth (the wrapper reads
    # the same file).
    BUZZ_PRIVATE_KEY="$(cat "$BUZZ_AGENT_DIR/$name.key")"; export BUZZ_PRIVATE_KEY
    BUZZ_AGENT_PROFILE="$name"; export BUZZ_AGENT_PROFILE
    exec 9>&-   # do NOT inherit the supervisor's flock — an orphaned buzz-acp
                # would otherwise hold it and wedge every future sweep
    exec "$BUZZ_ACP_BIN" >>"$LOGDIR/$name.log" 2>&1 ) &
  echo $! > "$pidf"
  log "started $name pid $!"
}

sweep() {
  install_wrapper
  [ -f "$BUZZ_AGENT_DIR/enabled" ] || return 0
  while IFS= read -r name; do
    name=$(printf '%s' "$name" | tr -d '[:space:]')
    [ -n "$name" ] || continue
    case "$name" in \#*) continue ;; esac
    start_one "$name"
  done < "$BUZZ_AGENT_DIR/enabled"
}

sweep
[ "${1:-}" = "--once" ] && exit 0
while :; do
  sleep 15
  sweep
done
