#!/usr/bin/env bash
# Shrink the running fleet: check and remove the relay chain, then remove
# exit B, proving every retired resource is gone and the rest still works.
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

SUBSCRIPTION_TEST=$SYSTEMLAB_ROOT/scripts/test-subscription.py
ASSERT_RETIRED=$SYSTEMLAB_ROOT/scripts/assert-retired.py
SNAPSHOT=/tmp/meridian-systemlab-bindings.json
RELAY_CHECK_OUT=/tmp/meridian-systemlab-relay-check.json
TEST_OUT=/tmp/meridian-systemlab-test.json

for host in "$RELAY_A_IP" "$RELAY_B_IP"; do
  if ! meridian --json check "$host" >"$RELAY_CHECK_OUT-$host"; then
    cat "$RELAY_CHECK_OUT-$host"
    echo "check of relay $host did not pass before removal" >&2
    exit 1
  fi
done
python3 - "$RELAY_CHECK_OUT-$RELAY_A_IP" "$RELAY_CHECK_OUT-$RELAY_B_IP" <<'PYEOF'
import json
import sys

reports = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["command"] == "check", payload["command"]
    reports.extend(payload["data"]["reports"])
assert [report["scope"] for report in reports] == ["relay", "relay"], reports
assert all(report["verdict"] == "passed" for report in reports), reports
# Two Realm hops, each leg's throughput toward its next hop, and the public entry.
assert sum(report["counts"]["passed"] for report in reports) == 5, reports
assert sum(report["counts"]["checks"] for report in reports) == 5, reports
PYEOF
pass "check passed every hop of relay-chain"

python3 "$ASSERT_RETIRED" snapshot "$SNAPSHOT"
meridian relay remove "$RELAY_A_IP" --yes
python3 "$ASSERT_RETIRED" verify "$SNAPSHOT" \
  --expect realm_hop \
  --expect firewall_rule \
  --expect nginx_artifact \
  --expect host
pass "relay removal retired Realm units, firewall rules, exit listener, and Hosts"

for host in "$RELAY_A_IP" "$RELAY_B_IP"; do
  require_command \
    "no Realm unit left on $host" \
    ssh root@"$host" \
    "! systemctl list-units --all --no-legend 'netfwd-*.service' | grep -q netfwd-"
done

set +e
meridian --json check "$RELAY_A_IP" >"$RELAY_CHECK_OUT-$RELAY_A_IP"
set -e
python3 - "$RELAY_CHECK_OUT-$RELAY_A_IP" <<'PYEOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert [report["scope"] for report in payload["data"]["reports"]] == ["preflight"], payload["data"]
PYEOF
pass "the removed relay server is no longer checked as a relay"

meridian plan
pass "plan converged after relay removal"

python3 "$ASSERT_RETIRED" snapshot "$SNAPSHOT"
meridian node remove "$EXIT_B_IP" --yes
python3 "$ASSERT_RETIRED" verify "$SNAPSHOT" \
  --expect node_runtime \
  --expect node_binding \
  --expect config_profile \
  --expect service_user \
  --expect internal_squad \
  --expect firewall_rule \
  --expect nginx_artifact \
  --expect host
pass "exit removal retired the node runtime, panel node/profile, bridge, firewall, nginx, and Hosts"

require_command \
  "control plane still running on exit A" \
  ssh root@"$EXIT_A_IP" \
  "docker ps --format '{{.Names}}' | grep -qx ctlsvc-api"

meridian plan
pass "plan converged after exit removal"

wait_for_command \
  "remaining client paths after exit B removal" \
  90 \
  python3 "$SUBSCRIPTION_TEST" \
  --address "$EXIT_A_IP" \
  --address "$GATEWAY_IP" \
  --forbid-address "$EXIT_B_IP" \
  --forbid-address "$RELAY_A_IP" \
  --forbid-address "$RELAY_B_IP"
pass "remaining client paths connect and removed endpoints are gone"

set +e
meridian --json test "$EXIT_A_IP" >"$TEST_OUT"
TEST_STATUS=$?
set -e
python3 - "$TEST_OUT" "$TEST_STATUS" <<'PYEOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
checks = (payload.get("data") or {}).get("checks", [])
codes = [finding["code"] for check in checks for finding in check["findings"]]
# Lab exits egress through Docker NAT, so an exit-IP mismatch still proves traffic flowed.
broken = [code for code in codes if code in {"PROTOCOL_TRAFFIC_FAILED", "PROTOCOL_CONFIG_INVALID"}]
flowed = [code for code in codes if code in {"PROTOCOL_TRAFFIC_OK", "PROTOCOL_EGRESS_MISMATCH"}]
if broken or not flowed:
    print(json.dumps(payload, indent=2))
    raise SystemExit(f"meridian test exited {sys.argv[2]} with broken traffic: {broken or 'no traffic evidence'}")
PYEOF
pass "meridian test pushed traffic through the remaining fleet"

python3 "$GOLDEN_CAPTURE" --topology reduced --user acceptance
pass "golden subscription corpus captured for exit A and the routing gateway"
