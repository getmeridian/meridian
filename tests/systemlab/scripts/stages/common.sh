#!/usr/bin/env bash

set -euo pipefail

SYSTEMLAB_ROOT=/workspace/tests/systemlab
INTENT_PATH=/tmp/meridian-v4-systemlab-intent.json

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

wait_for_node_api() {
  local host=$1
  local elapsed=0
  while [ "$elapsed" -lt 90 ]; do
    if ssh root@"$host" \
      "docker ps --filter name=remnawave-node --format '{{.Status}}' | grep -q Up && ss -tlnp sport = :3010 | grep -q LISTEN" \
      >/dev/null 2>&1
    then
      sleep 5
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "Node API did not become ready on $host" >&2
  return 1
}

restart_node() {
  local host=$1
  ssh root@"$host" docker start remnawave-node >/dev/null
  wait_for_node_api "$host"
}
