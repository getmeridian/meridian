#!/usr/bin/env bash
# A web panel on its own domain beside an exit on the same server (#110): the panel's
# domain certificate and the server's IP certificate must never share files, XHTTP over
# the IP must get the IP's certificate, and XHTTP on the panel's domain must compile and
# join the panel. Pebble issues without DNS; compose aliases the name to exit B.
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

PANEL_DOMAIN=panel.lab.test
DOMAIN_INTENT=/tmp/meridian-panel-domain-intent.json
FLEET_FILE=/tmp/meridian-panel-domain-fleet.yml
CHECK_OUT=/tmp/meridian-panel-domain-check.json

python3 - "$EXIT_B_IP" "$PANEL_DOMAIN" "$DOMAIN_INTENT" <<'PYEOF'
import sys

from meridian.config import SERVER_PROFILES_FILE
from meridian.core.topology import SetupIntent
from meridian.servers import ServerRegistry
from meridian.setup.single_server import single_server_intent

address, panel_domain, output = sys.argv[1:]
entry = ServerRegistry(SERVER_PROFILES_FILE).find(address)
assert entry is not None, f"{address} is not on the server shelf"
intent = single_server_intent(None, server_ref=entry.id, address=address, client_name="solo")
data = intent.model_dump(mode="json")
data["control"]["public_hostname"] = panel_domain
with open(output, "w", encoding="utf-8") as handle:
    handle.write(SetupIntent.model_validate(data).model_dump_json(indent=2))
PYEOF
# Setup's own verification validates every published TLS endpoint's certificate.
if ! meridian setup --restart --intent "$DOMAIN_INTENT" --yes; then
  wait_for_command "resumed panel-domain setup" 90 meridian setup --yes
fi
pass "panel on its own domain with IP-addressed XHTTP deployed and verified"

served_names() {
  # $1: -servername NAME or -noservername; $2: -checkhost NAME or -checkip ADDRESS
  echo | openssl s_client -connect "$EXIT_B_IP:443" $1 2>/dev/null | openssl x509 -noout $2
}
served_names "-servername $PANEL_DOMAIN" "-checkhost $PANEL_DOMAIN" | grep -q "does match certificate"
served_names "-noservername" "-checkip $EXIT_B_IP" | grep -q "does match certificate"
installs=$(ssh root@"$EXIT_B_IP" "grep -h Le_RealFullChainPath /root/.acme.sh/*/*.conf" | sort)
test "$(echo "$installs" | wc -l)" -eq 2
test "$(echo "$installs" | sort -u | wc -l)" -eq 2
pass "the panel serves its domain's certificate, IP clients the IP's, from separate files"

assert_tls_checks() {
  meridian --json check "$EXIT_B_IP" >"$CHECK_OUT" || true
  python3 - "$CHECK_OUT" "$@" <<'PYEOF'
import json
import sys

path, *names = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
checks = [check for report in payload["data"]["reports"] for check in report["checks"]]
tls = {check["name"]: check["status"] for check in checks if check["id"].startswith("tls:")}
assert tls and all(status == "passed" for status in tls.values()), tls
for name in names:
    assert any(name in label for label in tls), (name, tls)
PYEOF
}
assert_tls_checks "$PANEL_DOMAIN" "$EXIT_B_IP"
meridian plan >/dev/null
pass "check verifies both certified names and the plan is converged"

meridian fleet export >"$FLEET_FILE"
sed -i -e "s/tls_sni: $EXIT_B_IP\$/tls_sni: $PANEL_DOMAIN/" -e "s/host: $EXIT_B_IP\$/host: $PANEL_DOMAIN/" "$FLEET_FILE"
grep -q "tls_sni: $PANEL_DOMAIN" "$FLEET_FILE"
meridian fleet import "$FLEET_FILE" --yes
served_names "-servername $PANEL_DOMAIN" "-checkhost $PANEL_DOMAIN" | grep -q "does match certificate"
ssh root@"$EXIT_B_IP" "! grep -qs \"Le_Domain='$EXIT_B_IP'\" /root/.acme.sh/*/*.conf"
assert_tls_checks
meridian plan >/dev/null
pass "XHTTP and Hysteria2 moved onto the panel's domain, and the unused IP certificate was retired"

meridian teardown "$EXIT_B_IP" --yes
test ! -e "$MERIDIAN_HOME/cluster.yml"
meridian server add "$EXIT_B_IP" --name exit-b
python3 "$SYSTEMLAB_ROOT/scripts/write-intent.py" "$INTENT_PATH"
pass "panel-domain fleet torn down; server shelf and multi-server intent restored"
