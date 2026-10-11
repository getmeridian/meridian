#!/usr/bin/env bash
# One-command path: `meridian deploy IP` on a fresh server, end-user handoff,
# live verification, then a full teardown before the multi-server stages.
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

meridian deploy "$EXIT_B_IP" --client-name solo --yes
pass "single-server deploy converged and verified"

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
assert [link.key for link in links] == ["reality"], [link.url for link in links]

page = httpx.get(page_url(cluster, user) + "config.json", timeout=20)
assert page.status_code == 200, (page.status_code, page.text[:200])
assert [entry["url"] for entry in page.json()["protocols"]] == [link.url for link in links]
PYEOF
pass "public subscription and connection page serve the deployed Reality link"

meridian test "$EXIT_B_IP"
pass "meridian test verified the live proxy"

meridian deploy "$EXIT_B_IP" --client-name solo --yes
pass "redeploy with the same settings is idempotent"

meridian teardown "$EXIT_B_IP" --yes
test ! -e "$MERIDIAN_HOME/cluster.yml"
ssh root@"$EXIT_B_IP" 'test ! -e /opt/remnawave && test ! -e /etc/nginx/stream.d/meridian.conf'
pass "teardown removed the deployment and local fleet state"

meridian server add "$EXIT_B_IP" --name exit-b
python3 "$SYSTEMLAB_ROOT/scripts/write-intent.py" "$INTENT_PATH"
pass "server shelf and multi-server intent restored"
