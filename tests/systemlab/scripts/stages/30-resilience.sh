#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

SUBSCRIPTION_TEST=$SYSTEMLAB_ROOT/scripts/test-subscription.py

restore_nodes() {
  ssh root@"$EXIT_A_IP" \
    docker start remnawave-node >/dev/null 2>&1 || true
  ssh root@"$EXIT_B_IP" \
    docker start remnawave-node >/dev/null 2>&1 || true
}
trap restore_nodes EXIT

ssh root@"$EXIT_A_IP" \
  docker stop remnawave-node >/dev/null
python3 "$SUBSCRIPTION_TEST" \
  --address "$GATEWAY_IP"
pass "server-side egress pool failed over to exit B"

python3 "$SUBSCRIPTION_TEST" --automatic
pass "canonical client survived primary exit failure"

ssh root@"$EXIT_B_IP" \
  docker stop remnawave-node >/dev/null
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
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(
    cluster.panel.url,
    cluster.panel.api_token,
) as panel:
    host = next(
        item
        for item in panel.list_hosts()
        if "MERIDIAN_V4_VISIBLE" in item.tags
    )
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
  ! grep -q "repair.*host:" <<<"$PLAN_OUTPUT"
then
  echo "$PLAN_OUTPUT"
  echo "Managed Host drift was not observed" >&2
  exit 1
fi
pass "managed Host drift produced a repair plan"

meridian apply --yes
meridian plan
pass "managed Host drift repaired"

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

python3 <<'PYEOF'
from meridian.cluster import ClusterConfig

cluster = ClusterConfig.load()
checkpoint = next(iter(cluster.action_checkpoints.values()))
checkpoint.status = "unknown"
checkpoint.last_error = "simulated controller interruption"
cluster.pending_generation = cluster.active_generation
cluster.pending_plan_hash = cluster.active_plan_hash
cluster.save()
PYEOF
meridian apply --yes
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
pass "unknown checkpoint resumed by re-observation"

python3 "$SUBSCRIPTION_TEST" --automatic
pass "canonical subscription still connects after repair and resume"
