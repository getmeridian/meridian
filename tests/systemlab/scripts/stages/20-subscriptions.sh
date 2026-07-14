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
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(
    cluster.panel.url,
    cluster.panel.api_token,
) as panel:
    user = panel.get_user("acceptance")
    assert user is not None and user.short_uuid
    for client_type in ("", "xray-json", "mihomo"):
        document = panel.fetch_subscription(
            user.short_uuid,
            client_type=client_type,
        )
        assert document.content.strip(), client_type or "base64"
PYEOF
pass "Remnawave serves Base64, Xray JSON, and Mihomo documents"
