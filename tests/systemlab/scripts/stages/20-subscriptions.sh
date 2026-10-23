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

# Proxied names resolve over DoH only. Sniff every interface on exit A (loopback too, where
# Docker's embedded resolver answers) while a client fetches the IP observers through it: no
# packet may carry a plain DNS question for them. The host resolver's own lookup of a control
# name (never a parent or child of an observer name) proves the capture sees plain DNS.
observer_names=$(python3 -c '
from urllib.parse import urlsplit
from meridian.config import CONNECT_TEST_URLS_IPV4, CONNECT_TEST_URLS_IPV6
print(",".join(sorted({urlsplit(url).hostname for url in CONNECT_TEST_URLS_IPV4 + CONNECT_TEST_URLS_IPV6})))
')
dns_control="meridian-dns-control-$RANDOM.lab.test"
ssh root@"$EXIT_A_IP" rm -f /tmp/dns-capture.ready /tmp/dns-capture.stop
ssh root@"$EXIT_A_IP" python3 - --names "$observer_names,$dns_control" \
  --ready /tmp/dns-capture.ready --stop /tmp/dns-capture.stop \
  < "$SYSTEMLAB_ROOT/dns_capture.py" > /tmp/dns-capture.json &
capture_pid=$!
wait_for_command "DNS capture on exit A" 30 ssh root@"$EXIT_A_IP" test -e /tmp/dns-capture.ready
python3 "$SUBSCRIPTION_TEST" --address "$EXIT_A_IP"
ssh root@"$EXIT_A_IP" getent hosts "$dns_control" >/dev/null || true
ssh root@"$EXIT_A_IP" touch /tmp/dns-capture.stop
wait "$capture_pid"
python3 - "$dns_control" "$observer_names" <<'PYEOF'
import json
import sys

control, observers = sys.argv[1], sys.argv[2].split(",")
counts = json.load(open("/tmp/dns-capture.json"))
print(f"  exit A capture: {counts}")
assert counts[control] > 0, "the capture saw no plain DNS at all, so it proves nothing"
leaked = {name: counts[name] for name in observers if counts[name]}
assert not leaked, f"exit A sent plain DNS questions for proxied names: {leaked}"
PYEOF
pass "exit A resolves proxied names over DoH, with no plain DNS on the wire"

python3 "$SUBSCRIPTION_TEST" --automatic
pass "canonical Xray client fallback connects"

python3 "$SUBSCRIPTION_TEST" \
  --address "$EXIT_A_IP" \
  --address "$EXIT_B_IP" \
  --address "$RELAY_A_IP" \
  --address "$GATEWAY_IP" \
  --automatic \
  --bogus-credentials \
  --expect-failure
pass "every canonical endpoint rejects invalid credentials"

python3 <<'PYEOF'
import os

from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel, XRAY_JSON_CLIENT_TYPE
from tests.systemlab.subscription_client import (
    MIHOMO_CLIENT_TYPE,
    SINGBOX_CLIENT_TYPE,
    base64_endpoint_addresses,
    base64_vless_user_ids,
    endpoint_addresses,
    mihomo_endpoint_addresses,
    mihomo_vless_user_ids,
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_singbox_subscription,
    parse_xray_subscription,
    singbox_endpoint_addresses,
    singbox_vless_user_ids,
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
    xray_document = panel.fetch_subscription(user.short_uuid, client_type=XRAY_JSON_CLIENT_TYPE)
    mihomo_document = panel.fetch_subscription(user.short_uuid, client_type=MIHOMO_CLIENT_TYPE)
    singbox_document = panel.fetch_subscription(user.short_uuid, client_type=SINGBOX_CLIENT_TYPE)

base64_urls = parse_base64_subscription(base64_document.content)
xray_config = parse_xray_subscription(xray_document.content)
mihomo_config = parse_mihomo_subscription(mihomo_document.content)
singbox_config = parse_singbox_subscription(singbox_document.content)

# The link handed to end users must serve the same subscription without
# revealing the admin panel's secret path (in the URL or response headers).
import httpx

public_url = cluster.panel.subscription_url(user.short_uuid)
assert public_url and cluster.panel.secret_path not in public_url, public_url
public = httpx.get(public_url, timeout=20)
assert public.status_code == 200, (public.status_code, public.text[:200])
assert set(parse_base64_subscription(public.text)) == set(base64_urls)
assert cluster.panel.secret_path not in str(public.headers), dict(public.headers)

# Setup's handoff published a connection page listing the same links.
from meridian.handoff import page_url

page_config = httpx.get(page_url(cluster, user) + "config.json", timeout=20)
assert page_config.status_code == 200, (page_config.status_code, page_config.text[:200])
page = page_config.json()
assert page["subscription_url"] == public_url
assert {entry["url"] for entry in page["protocols"]} == set(base64_urls)
addresses_by_format = {
    "base64": base64_endpoint_addresses(base64_urls),
    "xray-json": endpoint_addresses(xray_config),
    "mihomo": mihomo_endpoint_addresses(mihomo_config),
    "singbox": singbox_endpoint_addresses(singbox_config),
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
    "singbox": singbox_vless_user_ids(singbox_config),
}
assert vless_ids_by_format["xray-json"]
assert len({frozenset(ids) for ids in vless_ids_by_format.values()}) == 1, vless_ids_by_format
PYEOF
pass "Base64, Xray JSON, Mihomo, and sing-box agree on endpoints and VLESS credentials"

# Parsing proves shape; the clients' own engines prove they would load it.
python3 "$SYSTEMLAB_ROOT/scripts/check-client-engines.py"
pass "sing-box check and mihomo -t accept the delivered documents"

# A relay forwards raw TCP. A probe through it without SNI must land on the
# camouflage site, never on the exit's own TLS server (its IP certificate).
relay_cert=$(echo | openssl s_client -connect "$RELAY_A_IP:443" -noservername 2>/dev/null \
  | openssl x509 -noout -text 2>/dev/null || true)
test -n "$relay_cert"
if printf '%s' "$relay_cert" | grep -qF "$EXIT_A_IP"; then
  echo "relay $RELAY_A_IP revealed the exit certificate for $EXIT_A_IP" >&2
  exit 1
fi
pass "relay without SNI shows the camouflage certificate, not the exit's"
