#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

SUBSCRIPTION_TEST=$SYSTEMLAB_ROOT/scripts/test-subscription.py
export INTERRUPTED_HOST_RESOURCE=host:exit-a:exit-a-reality:direct
INTERRUPTION_MARKER=/tmp/meridian-systemlab-after-apply.marker
INTERRUPTED_APPLY_LOG=/tmp/meridian-systemlab-interrupted-apply.log

restore_nodes() {
  ssh root@"$EXIT_A_IP" \
    docker start remnawave-node >/dev/null 2>&1 || true
  ssh root@"$EXIT_B_IP" \
    docker start remnawave-node >/dev/null 2>&1 || true
}
trap restore_nodes EXIT

ssh root@"$EXIT_A_IP" \
  docker stop remnawave-node >/dev/null
wait_for_command \
  "leastPing pool to route through exit B after exit A stops" \
  90 \
  python3 "$SUBSCRIPTION_TEST" --address "$GATEWAY_IP"
pass "gateway probe traversed the leastPing pool with only exit B available"

python3 "$SUBSCRIPTION_TEST" --automatic
pass "canonical client survived primary exit failure"

restart_node "$EXIT_A_IP"
ssh root@"$EXIT_B_IP" \
  docker stop remnawave-node >/dev/null
wait_for_command \
  "leastPing pool to route through exit A after exit B stops" \
  90 \
  python3 "$SUBSCRIPTION_TEST" --address "$GATEWAY_IP"
pass "gateway probe traversed the leastPing pool with only exit A available"

ssh root@"$EXIT_A_IP" \
  docker stop remnawave-node >/dev/null
wait_for_command \
  "leastPing pool to fail closed after both exits stop" \
  90 \
  python3 "$SUBSCRIPTION_TEST" \
    --address "$GATEWAY_IP" \
    --expect-failure
pass "server-side routing failed closed with all exits down"

python3 "$SUBSCRIPTION_TEST" \
  --automatic \
  --expect-failure
pass "canonical client blocked when every path was down"

restart_node "$EXIT_A_IP"
restart_node "$EXIT_B_IP"
trap - EXIT
pass "exit runtimes recovered"

python3 <<'PYEOF'
import os

from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
bindings = [
    binding
    for binding in cluster.managed_bindings.values()
    if binding.active
    and binding.logical_id == os.environ["INTERRUPTED_HOST_RESOURCE"]
]
assert len(bindings) == 1 and bindings[0].remote_id
with MeridianPanel(
    cluster.panel.url,
    cluster.panel.api_token,
) as panel:
    host = panel.get_host(bindings[0].remote_id)
    assert host is not None
    assert "MERIDIAN_V4_VISIBLE" in host.tags
    panel.update_host(
        host.uuid,
        remark=host.remark,
        address=host.address,
        port=host.port,
        config_profile_uuid=host.config_profile_uuid,
        inbound_uuid=host.inbound_uuid,
        sni=host.sni,
        host_header=host.host,
        path=host.path,
        alpn=host.alpn or None,
        fingerprint=host.fingerprint or None,
        security_layer=host.security_layer,
        is_disabled=True,
        tags=host.tags,
        is_hidden=host.is_hidden,
        xray_json_template_uuid=host.xray_json_template_uuid,
        exclude_from_subscription_types=(
            host.exclude_from_subscription_types
        ),
    )
PYEOF

set +e
PLAN_OUTPUT=$(meridian plan 2>&1)
PLAN_STATUS=$?
set -e
if [ "$PLAN_STATUS" -ne 2 ] || \
  ! grep -q "repair.*host:" <<<"$PLAN_OUTPUT" || \
  ! grep -Fq "$INTERRUPTED_HOST_RESOURCE" <<<"$PLAN_OUTPUT"
then
  echo "$PLAN_OUTPUT"
  echo "Managed Host drift was not observed" >&2
  exit 1
fi
pass "managed Host drift produced a repair plan"

rm -f "$INTERRUPTION_MARKER" "$INTERRUPTED_APPLY_LOG"
cleanup_interrupted_apply() {
  if [ -n "${INTERRUPTED_APPLY_PID:-}" ]; then
    kill -9 "$INTERRUPTED_APPLY_PID" >/dev/null 2>&1 || true
    wait "$INTERRUPTED_APPLY_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup_interrupted_apply EXIT

MERIDIAN_TEST_AFTER_APPLY_RESOURCE="$INTERRUPTED_HOST_RESOURCE" \
MERIDIAN_TEST_AFTER_APPLY_MARKER="$INTERRUPTION_MARKER" \
  meridian apply --yes >"$INTERRUPTED_APPLY_LOG" 2>&1 &
INTERRUPTED_APPLY_PID=$!

MARKER_READY=0
for _ in $(seq 1 90); do
  if [ -f "$INTERRUPTION_MARKER" ]; then
    MARKER_READY=1
    break
  fi
  if ! kill -0 "$INTERRUPTED_APPLY_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if [ "$MARKER_READY" -ne 1 ]; then
  cat "$INTERRUPTED_APPLY_LOG"
  echo "Apply did not reach the after-mutation interruption marker" >&2
  exit 1
fi
if ! kill -0 "$INTERRUPTED_APPLY_PID" >/dev/null 2>&1; then
  cat "$INTERRUPTED_APPLY_LOG"
  echo "Apply exited instead of blocking after the Host mutation" >&2
  exit 1
fi

kill -9 "$INTERRUPTED_APPLY_PID"
set +e
wait "$INTERRUPTED_APPLY_PID"
INTERRUPTED_APPLY_STATUS=$?
set -e
INTERRUPTED_APPLY_PID=
trap - EXIT
unset MERIDIAN_TEST_AFTER_APPLY_RESOURCE MERIDIAN_TEST_AFTER_APPLY_MARKER
if [ "$INTERRUPTED_APPLY_STATUS" -eq 0 ]; then
  cat "$INTERRUPTED_APPLY_LOG"
  echo "Interrupted apply unexpectedly exited successfully" >&2
  exit 1
fi
pass "apply was killed after the managed Host mutation"

python3 <<'PYEOF'
import os

from meridian.cluster import ClusterConfig

cluster = ClusterConfig.load()
checkpoint = next(
    checkpoint
    for checkpoint in cluster.action_checkpoints.values()
    if checkpoint.resource_id == os.environ["INTERRUPTED_HOST_RESOURCE"]
)
assert checkpoint.status == "running"
assert cluster.pending_plan_hash
assert cluster.pending_generation > 0
PYEOF
pass "interrupted apply left a durable running checkpoint"

meridian apply --yes
meridian plan
python3 <<'PYEOF'
from meridian.cluster import ClusterConfig

cluster = ClusterConfig.load()
assert not cluster.pending_plan_hash
assert cluster.pending_generation == 0
assert all(
    checkpoint.status == "succeeded"
    for checkpoint in cluster.action_checkpoints.values()
)
PYEOF
python3 "$SUBSCRIPTION_TEST" --automatic
rm -f "$INTERRUPTION_MARKER" "$INTERRUPTED_APPLY_LOG"
pass "fresh apply re-observed the Host mutation, converged, and preserved connectivity"

UNMANAGED_UUID=$(python3 <<'PYEOF'
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(
    cluster.panel.url,
    cluster.panel.api_token,
) as panel:
    squad = panel.create_external_squad(
        "systemlab-unmanaged"
    )
    print(squad.uuid)
PYEOF
)
meridian apply --yes
export UNMANAGED_UUID
python3 <<'PYEOF'
import os

from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(
    cluster.panel.url,
    cluster.panel.api_token,
) as panel:
    squad = panel.get_external_squad(
        os.environ["UNMANAGED_UUID"]
    )
    assert squad is not None
    panel.delete_external_squad(squad.uuid)
PYEOF
pass "unmanaged Remnawave resource remained untouched"
