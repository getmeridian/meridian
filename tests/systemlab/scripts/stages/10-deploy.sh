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

require_command \
  "control service running on control/exit A" \
  ssh root@"$EXIT_A_IP" systemctl is-active --quiet ctld

for host in "$EXIT_A_IP" "$EXIT_B_IP" "$GATEWAY_IP"; do
  wait_for_agent "$host"
done
pass "agents and cores run on both exits and the routing gateway"

PROXY_NAMES='remna|xray|vless|vpn|proxy|meridian'
UNIT_AND_OPT_NAMES="! systemctl list-units --all --no-legend --plain | awk '{print \$1}' | grep -Eqi '$PROXY_NAMES' && ! ls /opt | grep -Eqi '$PROXY_NAMES'"
require_command \
  "no proxy names in unit or /opt names on control/exit A" \
  ssh root@"$EXIT_A_IP" "$UNIT_AND_OPT_NAMES"
# The control service runs as `meridian serve`; only the nodes it manages hide their processes too.
for host in "$EXIT_B_IP" "$GATEWAY_IP"; do
  require_command \
    "no proxy names in unit, process, or /opt names on $host" \
    ssh root@"$host" "$UNIT_AND_OPT_NAMES && ! ps -eo comm= | grep -Eqi '$PROXY_NAMES'"
done

FLEET_STATUS_OUT=/tmp/meridian-systemlab-fleet-status.json
# Exit 3 is expected: a relay route stays unverified until a client goes through it.
status_rc=0
meridian --json fleet status >"$FLEET_STATUS_OUT" || status_rc=$?
python3 - "$FLEET_STATUS_OUT" "$status_rc" <<'PYEOF'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
data, counts = payload["data"], payload["summary"]["counts"]
problems = []
if sys.argv[2] not in {"0", "3"}:
    problems.append(f"fleet status exited {sys.argv[2]}")
if not data["panel"]["healthy"]:
    problems.append("control service not healthy")
names = sorted(node["name"] for node in data["nodes"] if node["status"] == "connected")
if names != ["exit-a", "exit-b", "gateway-a"]:
    problems.append(f"connected nodes {names}")
for key in ("failed_nodes", "disconnected_nodes", "unknown_nodes", "unhealthy_relays"):
    if counts[key]:
        problems.append(f"{key}={counts[key]}")
if problems:
    print(json.dumps(payload, indent=2))
    sys.exit("fleet status: " + "; ".join(problems))
PYEOF
pass "fleet status reports every agent live and applied"

for host in "$RELAY_A_IP" "$RELAY_B_IP"; do
  require_command \
    "Realm active on $host" \
    ssh root@"$host" \
    "systemctl list-units --state=active --no-legend 'netfwd-*.service' | grep -q netfwd-"
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
