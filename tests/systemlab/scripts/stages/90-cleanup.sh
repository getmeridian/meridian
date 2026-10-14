#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

cleanup_failed=0
for host in "$EXIT_A_IP" "$EXIT_B_IP" "$GATEWAY_IP"; do
  if ! require_command \
    "nested Docker state cleared on $host" \
    ssh root@"$host" '
      set -eu
      containers=$(docker ps -aq)
      if [ -n "$containers" ]; then
        docker rm -f $containers >/dev/null
      fi
      docker volume prune -af >/dev/null
      docker network prune -f >/dev/null
    '
  then
    cleanup_failed=1
  fi
done

if [ "$cleanup_failed" -ne 0 ]; then
  echo "Nested Docker cleanup failed on one or more hosts." >&2
  exit 1
fi

pass "nested Docker state cleared; cached images retained"
