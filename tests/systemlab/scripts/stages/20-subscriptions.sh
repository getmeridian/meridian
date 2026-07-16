#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

SUBSCRIPTION_TEST=$SYSTEMLAB_ROOT/scripts/test-subscription.py

python3 "$SUBSCRIPTION_TEST" \
  --address "$EXIT_A_IP" \
  --address "$EXIT_B_IP" \
  --address "$RELAY_A_IP" \
  --address "$GATEWAY_IP" \
  --forbid-address "$RELAY_B_IP"
pass "canonical subscription executes every advertised V4 path"

python3 "$SUBSCRIPTION_TEST" --automatic
pass "canonical Xray client fallback connects"

python3 "$SUBSCRIPTION_TEST" \
  --automatic \
  --bogus-credentials \
  --expect-failure
pass "canonical endpoints reject invalid credentials"

python3 <<'PYEOF'
import os

from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel
from tests.systemlab.subscription_client import (
    base64_endpoint_addresses,
    base64_vless_user_ids,
    endpoint_addresses,
    mihomo_endpoint_addresses,
    mihomo_vless_user_ids,
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_xray_subscription,
    xray_vless_user_ids,
)

cluster = ClusterConfig.load()
with MeridianPanel(
    cluster.panel.url,
    cluster.panel.api_token,
) as panel:
    user = panel.get_user("acceptance")
    assert user is not None and user.short_uuid
    base64_document = panel.fetch_subscription(user.short_uuid)
    xray_document = panel.fetch_subscription(user.short_uuid, client_type="xray-json")
    mihomo_document = panel.fetch_subscription(user.short_uuid, client_type="mihomo")

base64_urls = parse_base64_subscription(base64_document.content)
xray_config = parse_xray_subscription(xray_document.content)
mihomo_config = parse_mihomo_subscription(mihomo_document.content)
addresses_by_format = {
    "base64": base64_endpoint_addresses(base64_urls),
    "xray-json": endpoint_addresses(xray_config),
    "mihomo": mihomo_endpoint_addresses(mihomo_config),
}
expected = {
    os.environ["EXIT_A_IP"],
    os.environ["EXIT_B_IP"],
    os.environ["RELAY_A_IP"],
    os.environ["GATEWAY_IP"],
}
for client_type, addresses in addresses_by_format.items():
    assert expected <= addresses, (client_type, expected - addresses)
    assert os.environ["RELAY_B_IP"] not in addresses, client_type
vless_ids_by_format = {
    "base64": base64_vless_user_ids(base64_urls),
    "xray-json": xray_vless_user_ids(xray_config),
    "mihomo": mihomo_vless_user_ids(mihomo_config),
}
assert vless_ids_by_format["xray-json"]
assert len({frozenset(ids) for ids in vless_ids_by_format.values()}) == 1, vless_ids_by_format
PYEOF
pass "Base64, Xray JSON, and Mihomo agree on endpoints and VLESS credentials"
