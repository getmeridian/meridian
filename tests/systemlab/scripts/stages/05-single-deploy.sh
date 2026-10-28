#!/usr/bin/env bash
# One-command path: `meridian deploy IP` on a fresh server, end-user handoff,
# live verification, then a full teardown before the multi-server stages.
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

DEPLOY_LOG=/tmp/meridian-single-deploy.log
meridian deploy "$EXIT_B_IP" --client-name solo --yes 2>&1 | tee "$DEPLOY_LOG"
# Lab egress is NAT'd behind the host, so traffic passes with an egress warning.
grep -qE "Traffic verified end to end|Traffic works, but leaves through" "$DEPLOY_LOG"
pass "single-server deploy converged, verified, and carried real traffic"

python3 <<'PYEOF'
import httpx

from meridian.cluster import ClusterConfig
from meridian.handoff import page_url, subscription_protocol_urls
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
assert cluster.topology_intent is not None
assert cluster.topology_intent.access.users == ["solo"]
with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
    user = panel.get_user("solo")
assert user is not None and user.short_uuid

public_url = cluster.panel.subscription_url(user.short_uuid)
assert cluster.panel.secret_path not in public_url, public_url
subscription = httpx.get(public_url, timeout=20)
assert subscription.status_code == 200, (subscription.status_code, subscription.text[:200])
links = subscription_protocol_urls(subscription.text)
assert [link.key for link in links] == ["reality", "reality", "xhttp", "xhttp", "hysteria2"], [
    link.url for link in links
]

page = httpx.get(page_url(cluster, user) + "config.json", timeout=20)
assert page.status_code == 200, (page.status_code, page.text[:200])
assert [entry["url"] for entry in page.json()["protocols"]] == [link.url for link in links]
PYEOF
pass "public subscription and connection page serve the deployed Reality link"

python3 "$GOLDEN_CAPTURE" --topology single --user solo
pass "golden subscription corpus captured for the single-exit fleet"


meridian deploy "$EXIT_B_IP" --client-name solo --yes
pass "redeploy with the same settings is idempotent"

meridian client add bob
meridian client remove bob --yes
python3 <<'PYEOF'
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
assert cluster.topology_intent is not None
assert cluster.topology_intent.access.users == ["solo"], cluster.topology_intent.access.users
with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
    assert panel.get_user("bob") is None, "removed client still exists in the panel"
    assert panel.get_user("solo") is not None
PYEOF
pass "client remove retired the panel user and kept the others"

meridian teardown "$EXIT_B_IP" --yes
test ! -e "$MERIDIAN_HOME/cluster.yml"
ssh root@"$EXIT_B_IP" 'test ! -e /opt/ctlsvc && test ! -e /opt/netagent && test ! -e /etc/nginx/stream.d/meridian.conf'
pass "teardown removed the deployment and local fleet state"

meridian server add "$EXIT_B_IP" --name exit-b
python3 "$SYSTEMLAB_ROOT/scripts/write-intent.py" "$INTENT_PATH"
pass "server shelf and multi-server intent restored"
