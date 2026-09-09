#!/bin/sh
# buzz CLI wrapper. Hermes's terminal sandbox strips BUZZ_* from the child
# environment, so an @mentioned Hermes agent cannot get BUZZ_PRIVATE_KEY that
# way. This wrapper resolves the calling agent's key from a 0600 file by Hermes
# profile and injects it into the real CLI subprocess ONLY — never into this
# process's environment, never into the agent prompt.
#
# Deployed to /usr/local/bin/buzz by buzz-agent-supervise.sh (which also stages
# buzz.real). Test knobs: BUZZ_WRAP_KEYDIR, BUZZ_WRAP_REAL, BUZZ_WRAP_RELAY.
set -eu

keydir="${BUZZ_WRAP_KEYDIR:-/opt/data/buzz-agents}"
real="${BUZZ_WRAP_REAL:-/opt/data/bin/buzz.real}"
relay="${BUZZ_WRAP_RELAY:-wss://buzz.srv1608402.hstgr.cloud}"

# Which agent is calling? Use a Hermes profile marker — both HERMES_PROFILE and
# HERMES_HOME survive the terminal sandbox. Never key off a BUZZ_* var: the
# sandbox strips those before this wrapper ever runs.
profile="${HERMES_PROFILE:-}"
if [ -z "$profile" ]; then
  case "${HERMES_HOME:-}" in
    */profiles/*) profile=$(basename "$HERMES_HOME") ;;
    *)            profile=default ;;
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
