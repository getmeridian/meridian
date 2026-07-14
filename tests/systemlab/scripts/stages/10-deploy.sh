#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

if ! meridian setup \
  --intent "$INTENT_PATH" \
  --yes
then
  verified=0
  for _ in $(seq 1 30); do
    sleep 3
    if meridian setup --yes; then
      verified=1
      break
    fi
  done
  if [ "$verified" -ne 1 ]; then
    echo "Resumed setup never reached verification" >&2
    exit 1
  fi
fi
pass "reviewed setup applied and verified"

for container in \
  remnawave \
  remnawave-db \
  remnawave-redis \
  remnawave-node
do
  require_command \
    "$container running on control/exit A" \
    ssh root@"$EXIT_A_IP" \
    "docker ps --format '{{.Names}}' | grep -qx '$container'"
done

for host in "$EXIT_B_IP" "$GATEWAY_IP"; do
  wait_for_node_api "$host"
done
pass "both exits and routing gateway connected"

for host in "$RELAY_A_IP" "$RELAY_B_IP"; do
  require_command \
    "Realm active on $host" \
    ssh root@"$host" \
    "systemctl is-active meridian-relay | grep -qx active"
done

SECOND_APPLY=$(meridian --json apply --yes)
printf '%s' "$SECOND_APPLY" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["data"]["all_succeeded"] is True
assert payload["data"]["changed"] is False
'
pass "empty second apply converged"

meridian plan
pass "remote observation reports no managed drift"
