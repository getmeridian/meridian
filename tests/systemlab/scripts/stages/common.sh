#!/usr/bin/env bash
# Sourced by every stage, which reads these variables.
# shellcheck disable=SC2034

set -euo pipefail

SYSTEMLAB_ROOT=/workspace/tests/systemlab
INTENT_PATH=/tmp/meridian-v4-systemlab-intent.json
GOLDEN_DIFF=$SYSTEMLAB_ROOT/scripts/diff-golden.py

pass() {
  echo "    ✓ $1"
}

require_command() {
  local description=$1
  shift
  if "$@"; then
    pass "$description"
    return 0
  fi
  echo "    ✗ $description"
  return 1
}

all_server_ips() {
  printf '%s\n' \
    "$EXIT_A_IP" \
    "$EXIT_B_IP" \
    "$RELAY_A_IP" \
    "$RELAY_B_IP" \
    "$GATEWAY_IP"
}

# The control server's issuance root (Pebble's, fetched by 00-bootstrap) joins every node's
# CA bundle; agents trust that bundle through a unit drop-in, since Pebble is no WebPKI CA.
PEBBLE_ISSUER_CA=/usr/local/share/ca-certificates/pebble-issuer.crt
AGENT_CA_DROPIN=/etc/systemd/system/netagent.service.d/lab-ca.conf

# A node's agent and core both run, and the agent's latest report is applied and live.
wait_for_agent() {
  local host=$1
  local elapsed=0
  while [ "$elapsed" -lt 120 ]; do
    if ssh root@"$host" "systemctl is-active --quiet netagent && systemctl is-active --quiet netcore" \
      >/dev/null 2>&1
    then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "Node agent and core did not come up on $host" >&2
  ssh root@"$host" "systemctl status --no-pager netagent netcore; journalctl --no-pager -n 30 -u netagent -u netcore" >&2 || true
  return 1
}

# Take an exit down the way an outage would: the agent first (it would restart its core).
stop_node() {
  local host=$1
  ssh root@"$host" "systemctl stop netagent && systemctl stop netcore" >/dev/null
}

restart_node() {
  local host=$1
  ssh root@"$host" "systemctl start netagent" >/dev/null
  wait_for_agent "$host"
}

wait_for_command() {
  local description=$1
  local timeout=$2
  shift 2
  local deadline=$((SECONDS + timeout))
  local attempt=1
  while true; do
    echo "    waiting for $description (attempt $attempt)"
    if "$@"; then
      return 0
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "Timed out waiting for $description" >&2
      return 1
    fi
    sleep 5
    attempt=$((attempt + 1))
  done
}
