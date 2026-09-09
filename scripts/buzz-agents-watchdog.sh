#!/bin/bash
# Host-side liveness for the Buzz-agent bridges. Same pattern as the laptop_fs
# watchdog: a host cron job that reaches into the Hermes container. Runs every
# minute from the host crontab:
#
#   * * * * * /root/HermesPlusOpenbrain/scripts/buzz-agents-watchdog.sh
#
#   1. (as root) reinstall the buzz reply wrapper — /usr/local/bin is not a
#      Docker volume, so it is lost on container recreate.
#   2. (as hermes) run supervise.sh --once — ensures one buzz-acp per name in
#      /opt/data/buzz-agents/enabled, relaunching any that exited.
#
# All agent config + the supervisor + the wrapper source live under /opt/data
# (image-update durable). This script and the crontab line are the only
# host-side pieces.
set -uo pipefail

C="${BUZZ_WATCHDOG_CONTAINER:-hermes-agent-7qpk-hermes-agent-1}"
LOG="${BUZZ_WATCHDOG_LOG:-/var/log/buzz-agents-watchdog.log}"

docker exec "$C" install -m 0755 \
  /opt/data/buzz-agents/buzz-wrap.sh /usr/local/bin/buzz >>"$LOG" 2>&1

docker exec -u hermes -e HOME=/opt/data "$C" \
  /opt/data/buzz-agents/supervise.sh --once >>"$LOG" 2>&1
