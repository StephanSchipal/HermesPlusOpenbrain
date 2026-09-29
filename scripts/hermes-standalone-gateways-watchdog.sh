#!/bin/bash
# Host-side liveness for Hermes profiles that run their OWN gateway
# (`gateway.standalone: true` in the profile's config.yaml). Same pattern as the
# buzz-agents / laptop_fs watchdogs: a host cron job reaching into the container.
#
#   * * * * * /root/HermesPlusOpenbrain/scripts/hermes-standalone-gateways-watchdog.sh
#
# Why: since Hermes v0.21 the container runs ONE multiplexing gateway (the root
# `gateway-default` slot) for every profile, and container boot registers every
# named profile slot DOWN. But the multiplexer only serves WhatsApp on the
# default profile, and our WhatsApp lives on `openbrain` — so `openbrain` is
# opted out with `gateway.standalone: true`, and nothing in the image starts a
# standalone slot on boot (verified 2026-09-29: `hermes -p openbrain gateway
# start` does not survive a container restart). This brings the slot back up.
set -uo pipefail

C="${HERMES_WATCHDOG_CONTAINER:-hermes-agent-7qpk-hermes-agent-1}"
LOG="${HERMES_WATCHDOG_LOG:-/var/log/hermes-standalone-gateways-watchdog.log}"
PROFILES="${HERMES_STANDALONE_PROFILES:-openbrain}"

log() { echo "$(date -Is) $*" >>"$LOG"; }

# Container not running (restart in progress, update, ...) — try again next minute.
[ "$(docker inspect -f '{{.State.Running}}' "$C" 2>/dev/null)" = "true" ] || exit 0

for p in $PROFILES; do
  svc="/run/service/gateway-$p"
  # The slot is created by cont-init's reconcile; not there yet means still booting.
  docker exec "$C" test -d "$svc" 2>/dev/null || continue
  # Only act on profiles that are really standalone — otherwise a second gateway
  # would fight the multiplexer over the same profile.
  docker exec "$C" grep -qE '^[[:space:]]+standalone:[[:space:]]*true' \
    "/opt/data/profiles/$p/config.yaml" 2>/dev/null || { log "$p: not gateway.standalone — skipping"; continue; }
  if [ "$(docker exec "$C" /command/s6-svstat -u "$svc" 2>/dev/null)" != "true" ]; then
    docker exec "$C" sh -c "rm -f '$svc/down' && /command/s6-svc -u '$svc'" >>"$LOG" 2>&1 \
      && log "$p: gateway slot was down — started"
  fi
done
