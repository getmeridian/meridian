#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

report_failed_resources() {
  if python3 <<'PYEOF'
from meridian.cluster import ClusterConfig

cluster = ClusterConfig.load()
for checkpoint in cluster.action_checkpoints.values():
    if checkpoint.status in {"failed", "unknown"}:
        print(
            f"    {checkpoint.status.upper()} {checkpoint.resource_id}: "
            f"{checkpoint.last_error or 'no error detail'}"
        )
PYEOF
  then
    return 0
  fi
  echo "    WARN: Could not read failed resource checkpoints." >&2
  return 0
}

if ! meridian setup \
  --intent "$INTENT_PATH" \
  --yes
then
  report_failed_resources
  verified=0
  for _ in $(seq 1 30); do
    sleep 3
    if meridian setup --yes; then
      verified=1
      break
    fi
    report_failed_resources
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
    "systemctl list-units --state=active --no-legend 'meridian-realm-*.service' | grep -q meridian-realm-"
done

SECOND_APPLY_OUT=/tmp/meridian-systemlab-second-apply.json
SECOND_APPLY_ERR=/tmp/meridian-systemlab-second-apply.err
if ! meridian --json apply --yes >"$SECOND_APPLY_OUT" 2>"$SECOND_APPLY_ERR"; then
  echo "Second V4 apply failed:" >&2
  tail -n 20 "$SECOND_APPLY_ERR" >&2
  report_failed_resources
  exit 1
fi
SECOND_APPLY=$(cat "$SECOND_APPLY_OUT")
printf '%s' "$SECOND_APPLY" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
assert payload["command"] == "apply"
assert payload["status"] == "no_changes"
assert payload["exit_code"] == 0
assert payload["data"]["all_succeeded"] is True
'
pass "empty second apply converged"

meridian plan
pass "remote observation reports no managed drift"
