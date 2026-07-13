#!/usr/bin/env bash
# System lab controller — deploys Remnawave exit + relay, tests connections,
# verifies key preservation on redeploy, then tears down.
set -euo pipefail

EXIT_IP=${EXIT_IP:?}
RELAY_IP=${RELAY_IP:?}
export MERIDIAN_HOME=${MERIDIAN_HOME:-/tmp/meridian-home}
export PYTHONUNBUFFERED=1
mkdir -p "$MERIDIAN_HOME" /root/.ssh

PASS=0
FAIL=0
pass()      { PASS=$((PASS+1)); echo "    ✓ $1"; }
fail_test() { FAIL=$((FAIL+1)); echo "    ✗ FAIL: $1"; }

# Smart wait: poll until the remnawave-node container has xray running and
# the node API port (3010) is accepting connections. Port 443 is nginx
# (always listening) — NOT the right signal. Port 3010 is the node→panel
# mTLS API; it only binds after xray initializes Reality keys and inbounds.
wait_for_xray() {
  local host="$1"
  local max_wait="${2:-45}"
  local elapsed=0
  echo -n "    waiting for xray on $host..."
  while [ "$elapsed" -lt "$max_wait" ]; do
    # Two checks: (1) node container Up, (2) node API port 3010 listening.
    # Both must pass — container can be Up before xray finishes init.
    if ssh root@"$host" "docker ps --filter name=remnawave-node --format '{{.Status}}' | grep -q Up" 2>/dev/null && \
       ssh root@"$host" "ss -tlnp sport = :3010 2>/dev/null | grep -q LISTEN" 2>/dev/null; then
      # Grace period: port 3010 (node API) binds first, but Reality
      # inbounds (port ~10xxx, routed via nginx on 443) initialize later
      # after the node receives its config from the panel. On redeploy
      # the panel re-pushes config with retry/backoff, needing ~10-15s.
      sleep 15
      echo " ready ($((elapsed + 15))s)"
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  echo " timeout (${max_wait}s)"
  return 1
}

# ── Stage 1: SSH setup ───────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 1: SSH & systemd setup"
echo "═══════════════════════════════════════"

echo ">>> Scanning SSH host keys..."
for host in "$EXIT_IP" "$RELAY_IP"; do
  ok=0
  for i in $(seq 1 60); do
    if ssh-keyscan -T 2 "$host" >> /root/.ssh/known_hosts 2>/dev/null; then
      echo "    $host: key acquired"
      ok=1
      break
    fi
    sleep 2
  done
  [ "$ok" -eq 1 ] || { echo "FATAL: SSH not ready on $host after 2 min"; exit 1; }
done

cp /workspace/tests/systemlab/fixtures/id_ed25519 /root/.ssh/id_ed25519
chmod 600 /root/.ssh/id_ed25519

cat >/root/.ssh/config <<'EOF'
Host *
  User root
  IdentityFile /root/.ssh/id_ed25519
  BatchMode yes
  StrictHostKeyChecking yes
  ConnectTimeout 10
  LogLevel ERROR
EOF
chmod 600 /root/.ssh/config

# Wait for systemd boot on both nodes
for label_ip in "exit-node:$EXIT_IP" "relay-node:$RELAY_IP"; do
  label=${label_ip%%:*}; ip=${label_ip#*:}
  echo ">>> Waiting for systemd on $label..."
  for i in $(seq 1 30); do
    state=$(ssh root@"$ip" systemctl is-system-running 2>/dev/null || true)
    if [ "$state" = "running" ] || [ "$state" = "degraded" ]; then
      echo "    $label: $state"
      break
    fi
    [ "$i" -eq 30 ] && { echo "FATAL: $label systemd not ready"; exit 1; }
    sleep 2
  done
done

# Install Pebble CA on exit node (enables future domain-mode cert tests)
if [ -f /workspace/tests/systemlab/fixtures/pebble-ca.pem ]; then
  echo ">>> Installing Pebble root CA on exit node..."
  scp /workspace/tests/systemlab/fixtures/pebble-ca.pem root@"$EXIT_IP":/usr/local/share/ca-certificates/pebble.crt
  ssh root@"$EXIT_IP" update-ca-certificates 2>/dev/null
  echo "    Pebble CA trusted"
fi

# ── Stage 2: Fresh deploy ────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 2: Fresh deploy"
echo "═══════════════════════════════════════"

echo ">>> Deploying exit node ($EXIT_IP)..."
meridian deploy "$EXIT_IP" --user root --yes --no-harden --sni www.google.com

# ── Stage 3: Verify deployment ───────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 3: Verify deployment"
echo "═══════════════════════════════════════"

# Check Remnawave containers
for container in remnawave remnawave-db remnawave-redis remnawave-node; do
  if ssh root@"$EXIT_IP" docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
    pass "$container container running"
  else
    fail_test "$container container not running"
  fi
done

# remnawave-node MUST have NET_ADMIN capability — required by upstream Remnawave
# panel 2.6.2+ for node plugins (Torrent Blocker, Ingress/Egress Filter,
# Connection Drop) and the IP Control panel feature. These all push nftables
# rules into the host network namespace; without NET_ADMIN the kernel rejects
# them with EPERM and the panel UI silently reports nothing.
if ssh root@"$EXIT_IP" "docker inspect remnawave-node --format '{{range .HostConfig.CapAdd}}{{.}} {{end}}'" 2>/dev/null | grep -q "NET_ADMIN"; then
  pass "remnawave-node has NET_ADMIN capability (required for plugins + IP Control)"
else
  fail_test "remnawave-node missing NET_ADMIN — plugins and IP Control will silently fail"
fi

# Check nginx
if ssh root@"$EXIT_IP" nginx -t 2>&1 | grep -q "syntax is ok"; then
  pass "nginx config valid"
else
  fail_test "nginx config invalid"
fi

if ssh root@"$EXIT_IP" ss -tlnp sport = :443 2>/dev/null | grep -q LISTEN; then
  pass "port 443 listening"
else
  fail_test "port 443 not listening"
fi

# Check cluster config created
if [ -f "$MERIDIAN_HOME/cluster.yml" ]; then
  pass "cluster.yml created"
else
  fail_test "cluster.yml not created after deploy"
fi

# Check fleet status (node connected — poll instead of blind sleep)
echo ">>> Checking fleet status..."
wait_for_xray "$EXIT_IP" 15 || true
if meridian fleet status 2>&1 | grep -qi "connected"; then
  pass "node shows connected in fleet status"
else
  # Non-fatal — node may still be initializing
  echo "    WARN: node not yet connected in fleet status (may need more time)"
fi

# ── Stage 4: Client lifecycle ────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 4: Client lifecycle"
echo "═══════════════════════════════════════"

echo ">>> Adding clients alice and bob..."
if meridian client add alice 2>&1; then
  pass "client add alice"
else
  fail_test "client add alice"
fi

if meridian client add bob 2>&1; then
  pass "client add bob"
else
  fail_test "client add bob"
fi

echo ">>> Listing clients..."
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'alice' in names, f'alice not in {names}'
assert 'bob' in names, f'bob not in {names}'
" 2>/dev/null; then
  pass "client list shows alice and bob"
else
  fail_test "client list does not show both alice and bob"
fi

echo ">>> Showing clients..."
if meridian client show alice 2>&1; then
  pass "client show alice"
else
  fail_test "client show alice"
fi

if meridian client show bob 2>&1; then
  pass "client show bob"
else
  fail_test "client show bob"
fi

echo ">>> Removing alice (bob must survive)..."
if meridian client remove alice --yes 2>&1; then
  pass "client remove alice"
else
  fail_test "client remove alice"
fi

# Verify alice gone but bob remains
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'alice' not in names, f'alice still in {names}'
assert 'bob' in names, f'bob not in {names}'
" 2>/dev/null; then
  pass "alice removed, bob still exists"
else
  fail_test "client isolation failed after alice removal"
fi

echo ">>> Removing bob..."
if meridian client remove bob --yes 2>&1; then
  pass "client remove bob"
else
  fail_test "client remove bob"
fi

# Verify both gone (only default should remain)
if meridian client list 2>&1 | grep -qE "alice|bob"; then
  fail_test "alice or bob still appears after removal"
else
  pass "alice and bob fully removed"
fi

# ── Stage 5: PWA page serving ───────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 5: PWA page serving"
echo "═══════════════════════════════════════"

echo ">>> Getting default client VLESS UUID..."
# Remnawave has two UUIDs: database uuid and vless_uuid (for xray auth).
# Connection tests need vless_uuid. Fetch it from the panel API (same as ping.py).
CLIENT_UUID=$(python3 -c "
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel
c = ClusterConfig.load()
with MeridianPanel(c.panel.url, c.panel.api_token) as panel:
    users = panel.list_users()
    for u in users:
        if u.username == 'default':
            print(u.vless_uuid)
            break
    else:
        if users:
            print(users[0].vless_uuid)
" 2>/dev/null || true)

if [ -z "$CLIENT_UUID" ]; then
  fail_test "could not get default client UUID"
else
  pass "default client UUID: ${CLIENT_UUID:0:8}..."
fi

echo ">>> Extracting page path from nginx config..."
# The info_page_path is a random hex string baked into the nginx location block.
# Extract it by finding the location that aliases /var/www/private/.
INFO_PATH=$(ssh root@"$EXIT_IP" "grep -B2 'alias /var/www/private' /etc/nginx/conf.d/*.conf" 2>/dev/null \
  | grep -o 'location /[^/]*' | sed 's|location /||' | head -1 || true)

if [ -z "$INFO_PATH" ]; then
  fail_test "could not extract info_page_path from nginx config"
else
  pass "page path: $INFO_PATH"
fi

if [ -n "$CLIENT_UUID" ] && [ -n "$INFO_PATH" ]; then
  PAGE_URL="https://$EXIT_IP/$INFO_PATH/$CLIENT_UUID/"

  # Check if per-client page files exist on disk (generated by
  # `meridian client add` via the panel API, not by the provisioner)
  HAS_CLIENT_PAGE=$(ssh root@"$EXIT_IP" "test -f /var/www/private/$CLIENT_UUID/index.html && echo yes" 2>/dev/null || true)

  if [ "$HAS_CLIENT_PAGE" = "yes" ]; then
    echo ">>> Fetching connection page ($PAGE_URL)..."
    HTTP_CODE=$(curl -sk -o /tmp/page.html -D /tmp/headers.txt -w '%{http_code}' "$PAGE_URL" || true)

    if [ "$HTTP_CODE" = "200" ]; then
      pass "connection page returns 200"
    else
      fail_test "connection page returned HTTP $HTTP_CODE (expected 200)"
    fi

    # Verify HTML structure
    if grep -q '<link rel="manifest"' /tmp/page.html 2>/dev/null; then
      pass "page contains PWA manifest link"
    else
      fail_test "page missing PWA manifest link"
    fi

    if grep -q 'app\.js' /tmp/page.html 2>/dev/null; then
      pass "page references app.js"
    else
      fail_test "page missing app.js reference"
    fi

    # Verify config.json
    echo ">>> Fetching config.json..."
    CONFIG_CODE=$(curl -sk -o /tmp/config.json -w '%{http_code}' "${PAGE_URL}config.json" || true)

    if [ "$CONFIG_CODE" = "200" ]; then
      pass "config.json returns 200"
    else
      fail_test "config.json returned HTTP $CONFIG_CODE"
    fi

    if python3 -c "
import json
with open('/tmp/config.json') as f:
    data = json.load(f)
assert 'version' in data, 'missing version'
assert 'protocols' in data, 'missing protocols'
assert len(data['protocols']) > 0, 'empty protocols'
assert any(p.get('key') == 'reality' for p in data['protocols']), 'no reality protocol'
" 2>/dev/null; then
      pass "config.json valid (version, protocols with reality)"
    else
      fail_test "config.json missing expected fields"
    fi
  else
    echo "    Per-client pages not deployed (only shared PWA assets without a client add)"
  fi

  # Verify security headers on the PWA location (works even without per-client files)
  echo ">>> Checking security headers..."
  curl -sk -o /dev/null -D /tmp/headers.txt "https://$EXIT_IP/$INFO_PATH/pwa/app.js" || true
  if grep -qi 'content-security-policy' /tmp/headers.txt 2>/dev/null; then
    pass "Content-Security-Policy header present"
  else
    fail_test "missing Content-Security-Policy header"
  fi

  if grep -qi 'x-content-type-options.*nosniff' /tmp/headers.txt 2>/dev/null; then
    pass "X-Content-Type-Options: nosniff present"
  else
    fail_test "missing X-Content-Type-Options header"
  fi

  if grep -qi 'x-frame-options.*deny' /tmp/headers.txt 2>/dev/null; then
    pass "X-Frame-Options: DENY present"
  else
    fail_test "missing X-Frame-Options header"
  fi

  # Verify shared PWA assets
  echo ">>> Checking shared PWA assets..."
  for asset in pwa/app.js pwa/styles.css; do
    ASSET_CODE=$(curl -sk -o /dev/null -w '%{http_code}' "https://$EXIT_IP/$INFO_PATH/$asset" || true)
    if [ "$ASSET_CODE" = "200" ]; then
      pass "$asset accessible"
    else
      fail_test "$asset returned HTTP $ASSET_CODE"
    fi
  done
fi

# ── Stage 6: Relay deploy ────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 6: Relay deploy"
echo "═══════════════════════════════════════"

echo ">>> Deploying relay ($RELAY_IP -> $EXIT_IP)..."
meridian relay deploy "$RELAY_IP" --exit "$EXIT_IP" --sni www.google.com --yes

if ssh root@"$RELAY_IP" systemctl is-active meridian-relay 2>/dev/null | grep -q active; then
  pass "meridian-relay service active"
else
  fail_test "meridian-relay service not active"
fi

echo ">>> Checking relay in cluster config..."
if python3 -c "
from meridian.cluster import ClusterConfig
c = ClusterConfig.load()
ips = [r.ip for r in c.relays]
assert '$RELAY_IP' in ips, f'Relay not found. Relays: {ips}'
" 2>/dev/null; then
  pass "relay saved in cluster config"
else
  fail_test "relay not in cluster config"
fi

# ── Stage 7: Connection test ─────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 7: Connection test"
echo "═══════════════════════════════════════"

if [ -z "$CLIENT_UUID" ]; then
  fail_test "skipping connection tests — no client UUID"
else
  export CLIENT_UUID

  echo ">>> Running Reality connection tests..."
  wait_for_xray "$EXIT_IP" 30
  if python3 /workspace/tests/systemlab/scripts/test-connections.py; then
    pass "Reality connection tests passed"
  else
    fail_test "Reality connection tests failed"
  fi

  echo ">>> Testing rejection of bogus credentials..."
  if python3 << 'PYEOF'
import os, sys
from meridian.cluster import ClusterConfig
from meridian.xray_client import (
    build_test_configs_from_cluster,
    ensure_xray_binary,
    test_connection,
)

cluster = ClusterConfig.load()
configs = build_test_configs_from_cluster(
    cluster, os.environ["EXIT_IP"],
    uuid="00000000-0000-0000-0000-000000000000",
)
selected = [(l, c) for l, c, _ in configs if "Reality" in l]

if not selected:
    print("    SKIP: no Reality configs for negative test")
    sys.exit(0)

xray_bin = ensure_xray_binary()
if not xray_bin:
    print("    SKIP: no xray binary")
    sys.exit(0)

for label, config in selected:
    # Offset port to avoid any lingering process from the positive test
    config["inbounds"][0]["port"] += 100
    socks_port = config["inbounds"][0]["port"]
    ok, detail = test_connection(
        xray_bin, config, os.environ["EXIT_IP"], socks_port, label, False,
    )
    if ok:
        print(f"    FAIL: {label} accepted bogus UUID")
        sys.exit(1)
    print(f"    OK: {label} rejected bogus UUID ({detail})")
PYEOF
  then
    pass "server rejects bogus credentials"
  else
    fail_test "server accepted connection with bogus UUID"
  fi
fi

# ── Stage 8: Redeploy (key preservation) ─────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 8: Redeploy (key preservation)"
echo "═══════════════════════════════════════"

echo ">>> Capturing Reality keys before redeploy..."
BEFORE_PUB_KEY=$(meridian --json node list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
nodes = data if isinstance(data, list) else data.get('nodes', [])
for n in nodes:
    pk = n.get('reality_public_key', '') or n.get('public_key', '')
    if pk:
        print(pk)
        break
" 2>/dev/null || true)

if [ -z "$BEFORE_PUB_KEY" ]; then
  echo "    WARN: could not extract public key from node list (--json output may differ)"
  echo "    Falling back to cluster.yml..."
  BEFORE_PUB_KEY=$(python3 -c "
from meridian.cluster import ClusterConfig
c = ClusterConfig.load()
if c.nodes:
    print(c.nodes[0].reality_public_key)
" 2>/dev/null || true)
fi

if [ -n "$BEFORE_PUB_KEY" ]; then
  pass "captured before-key: ${BEFORE_PUB_KEY:0:12}..."
else
  fail_test "could not capture Reality public key before redeploy"
fi

echo ">>> Redeploying exit node (with hardening)..."
meridian deploy "$EXIT_IP" --user root --yes --sni www.google.com

echo ">>> Verifying keys preserved..."
AFTER_PUB_KEY=$(python3 -c "
from meridian.cluster import ClusterConfig
c = ClusterConfig.load()
if c.nodes:
    print(c.nodes[0].reality_public_key)
" 2>/dev/null || true)

if [ -n "$BEFORE_PUB_KEY" ] && [ "$BEFORE_PUB_KEY" = "$AFTER_PUB_KEY" ]; then
  pass "Reality keys preserved after redeploy"
else
  fail_test "Reality keys changed! Before=$BEFORE_PUB_KEY After=$AFTER_PUB_KEY"
fi

# Verify clients still work after redeploy
if [ -n "$CLIENT_UUID" ]; then
  echo ">>> Re-testing connections after redeploy..."
  # Xray node restarts during redeploy. Poll until it's actually listening
  # on port 443 instead of blind-sleeping. Usually ready in ~10s.
  wait_for_xray "$EXIT_IP" 45
  if python3 /workspace/tests/systemlab/scripts/test-connections.py; then
    pass "connections still work after redeploy"
  else
    fail_test "post-redeploy connection test failed"
  fi
fi

# ── Stage 9: Declarative plan/apply ──────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 9: Declarative plan/apply"
echo "═══════════════════════════════════════"

# Write a declarative cluster.yml v2 with desired state that matches
# the current panel state from Stage 4 (only the bootstrap default client).
echo ">>> Writing desired state to cluster.yml..."
CLUSTER_FILE="$MERIDIAN_HOME/cluster.yml"
# Read existing cluster.yml and append desired state
python3 -c "
import yaml, sys
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
# Keep the bootstrap default client; alice and bob were removed in Stage 4.
data['desired_clients'] = ['default']
data['version'] = 2
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
print('    desired_clients: [default]')
"

echo ">>> Running meridian plan..."
if meridian plan 2>&1; then
  pass "plan reports converged (default exists, desired_clients matches)"
else
  EXIT_CODE=$?
  if [ "$EXIT_CODE" -eq 2 ]; then
    fail_test "plan reports changes needed (exit 2) — expected convergence"
  else
    fail_test "plan failed unexpectedly (exit $EXIT_CODE)"
  fi
fi

# Add a new client via apply
echo ">>> Adding client 'charlie' via declarative apply..."
python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
data['desired_clients'] = ['default', 'charlie']
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
"

if meridian apply --yes 2>&1; then
  pass "apply added client charlie"
else
  fail_test "apply failed to add client charlie"
fi

# Verify charlie exists via client list (JSON)
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'charlie' in names, f'charlie not in {names}'
" 2>/dev/null; then
  pass "charlie visible in client list after apply"
else
  fail_test "charlie not found after apply"
fi

# Plan should now show converged
if meridian plan 2>&1; then
  pass "plan converged after apply"
else
  EXIT_CODE=$?
  if [ "$EXIT_CODE" -eq 2 ]; then
    fail_test "plan still shows changes after apply"
  fi
fi

# Clean up charlie
echo ">>> Removing charlie via declarative apply..."
python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
data['desired_clients'] = ['default']
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
"

# Charlie is "extras" from the diff's perspective (present on the panel,
# missing from desired_clients). `--yes` alone defaults to skip-extras for
# safety; explicit `--prune-extras=yes` opts in to the removal.
if meridian apply --yes --prune-extras=yes 2>&1; then
  pass "apply removed client charlie"
else
  fail_test "apply failed to remove client charlie"
fi

# Verify charlie is actually gone — REMOVE_CLIENT must be observable on the panel
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'charlie' not in names, f'charlie still in panel: {names}'
" 2>/dev/null; then
  pass "charlie actually absent from panel after remove apply"
else
  fail_test "charlie still present after apply removed it (cluster.yml says removed but panel disagrees)"
fi

# Drift safety: `meridian apply --yes` (no explicit --prune-extras) must NOT
# silently remove extras. Add a panel-only client and verify --yes alone
# leaves it alone, while --prune-extras=yes removes it.
echo ">>> Drift safety: --yes without --prune-extras must skip extras..."
python3 << 'PYEOF'
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
    squad_uuids = [cluster.squad_uuid] if cluster.squad_uuid else None
    panel.create_user("ghost-extras", squad_uuids=squad_uuids)
    print("    panel-side create of ghost-extras OK")
PYEOF

# `--yes` alone: safety default, should NOT remove ghost-extras.
meridian apply --yes 2>&1 >/dev/null
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'ghost-extras' in names, f'ghost-extras gone after --yes alone — safety default broken'
" 2>/dev/null; then
  pass "apply --yes alone preserved ghost-extras (safety default)"
else
  fail_test "apply --yes alone removed ghost-extras — safety default broken (--prune-extras must default to no under --yes)"
fi

# `--prune-extras=yes`: explicit opt-in, should remove ghost-extras.
meridian apply --yes --prune-extras=yes 2>&1 >/dev/null
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'ghost-extras' not in names, f'ghost-extras still in panel after --prune-extras=yes: {names}'
" 2>/dev/null; then
  pass "apply --yes --prune-extras=yes removed ghost-extras (explicit opt-in)"
else
  fail_test "apply --yes --prune-extras=yes did not remove ghost-extras"
fi

# State tracking: deliberate removal must work under --yes
# (from_extras=False because state-test-alice WAS in applied state)
echo ">>> State tracking: deliberate remove must work under --yes..."
# First: apply with desired_clients that includes state-test-alice
python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
data['desired_clients'] = ['default', 'state-test-alice']
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
"
meridian apply --yes --prune-extras=yes 2>&1 >/dev/null

# Verify state-test-alice exists
if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'state-test-alice' in names, f'state-test-alice not created: {names}'
" 2>/dev/null; then
  pass "state-test-alice created via apply"
else
  fail_test "state-test-alice not created"
fi

# Now: deliberately remove state-test-alice from desired and apply with plain --yes
python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
data['desired_clients'] = ['default']
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
"
# --yes alone: drift would be skipped, but INTENTIONAL removal must run
# (because state-test-alice was in the applied snapshot from previous apply)
meridian apply --yes 2>&1 >/dev/null

if meridian --json client list 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
clients = data if isinstance(data, list) else data.get('clients', [])
names = [c.get('username', '') for c in clients]
assert 'state-test-alice' not in names, f'still present: {names}'
" 2>/dev/null; then
  pass "state tracking: deliberate removal worked under --yes (from_extras=False)"
else
  fail_test "state tracking: deliberate removal SKIPPED under --yes — applied state not tracked"
fi

# Hybrid sync: imperative `client add` should also append to desired_clients
# when the user is managing clients declaratively. Otherwise the next plan
# would see the imperatively-added user as drift and remove them.
echo ">>> Hybrid: imperative client add should mirror into desired_clients..."
meridian client add hybrid-bob 2>&1 >/dev/null
if python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
desired = data.get('desired_clients') or []
assert 'hybrid-bob' in desired, f'hybrid-bob not in desired_clients: {desired}'
" 2>/dev/null; then
  pass "imperative client add mirrored into desired_clients (hybrid sync)"
else
  fail_test "imperative client add did NOT update desired_clients — next apply would remove hybrid-bob"
fi

# And plan must report converged (no REMOVE_CLIENT for hybrid-bob).
if meridian plan 2>&1 | grep -qE "remove.*hybrid-bob"; then
  fail_test "plan proposes removing hybrid-bob — hybrid sync did not actually close the gap"
else
  pass "plan reports converged after imperative add (no spurious REMOVE proposed)"
fi

# Symmetric: imperative remove should also drop from desired_clients.
meridian client remove hybrid-bob --yes 2>&1 >/dev/null || true
if python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
desired = data.get('desired_clients') or []
assert 'hybrid-bob' not in desired, f'hybrid-bob still in desired_clients: {desired}'
" 2>/dev/null; then
  pass "imperative client remove mirrored into desired_clients (hybrid sync)"
else
  fail_test "imperative client remove did NOT update desired_clients — next apply would re-create hybrid-bob"
fi

# Drift detection: panel-side change should be re-detected by plan.
# Manually delete the bootstrap user via the panel SDK and verify the next
# `meridian plan` proposes recreating it (since desired_clients still says
# 'default'). This is the core declarative property.
echo ">>> Drift test: deleting 'default' user directly via panel API..."
if ! python3 << 'PYEOF'
import sys
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
    user = panel.get_user('default')
    if user and user.uuid:
        panel.delete_user(user.uuid)
        print('    panel-side delete of default user OK')
    else:
        # Hard fail: without the panel delete succeeding, the subsequent drift
        # re-propose + apply assertions silently pass against a still-present
        # user. Don't let coverage rot — demand the setup actually executed.
        print('    FAIL: default user not found, cannot run drift test')
        sys.exit(1)
PYEOF
then
  fail_test "drift setup failed: default user not found on panel"
fi

# Plan should now show ADD_CLIENT default (or exit 2 = changes pending)
PLAN_OUT=$(meridian plan 2>&1 || true)
if echo "$PLAN_OUT" | grep -qE "add.*client.*default|default.*add"; then
  pass "drift detected: plan re-proposes default client"
else
  echo "$PLAN_OUT" | tail -5
  fail_test "drift NOT detected: plan didn't propose recreating default"
fi

# Apply should converge again — re-create default
if meridian apply --yes 2>&1; then
  pass "apply re-created default after drift"
else
  fail_test "apply failed to re-create default after drift"
fi

# Subscription page lifecycle through apply.
# Stage 5 only verifies the PWA / connection page; the Remnawave subscription
# page container path was untested end-to-end before this stage was added.
#
# The initial deploy in Stage 2 already brings the subscription page up, so
# we first DISABLE via apply (exercises REMOVE_SUBSCRIPTION_PAGE), verify
# the container is down, then re-ENABLE via apply (exercises
# ADD_SUBSCRIPTION_PAGE) and verify both the container is back up and the
# nginx proxy route serves a response.

echo ">>> Subscription page: disabling via cluster.yml + apply..."
python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
sp = data.get('subscription_page') or {}
sp['enabled'] = False
data['subscription_page'] = sp
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
"

if meridian apply --yes 2>&1; then
  pass "apply removed subscription page"
else
  fail_test "apply failed to remove subscription page"
fi

# `docker ps` filtering: an Up container will print 'Up ...'; a stopped or
# missing one prints nothing. We assert the running-status check fails.
if ! ssh root@"$EXIT_IP" "docker ps --filter name=remnawave-subscription-page --format '{{.Status}}' | grep -q Up" 2>/dev/null; then
  pass "subscription page container stopped after remove apply"
else
  fail_test "subscription page container still running after remove apply"
fi

echo ">>> Subscription page: re-enabling via cluster.yml + apply..."
python3 -c "
import yaml
with open('$CLUSTER_FILE') as f:
    data = yaml.safe_load(f)
sp = data.get('subscription_page') or {}
sp['enabled'] = True
data['subscription_page'] = sp
with open('$CLUSTER_FILE', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
"

if meridian apply --yes 2>&1; then
  pass "apply re-deployed subscription page container"
else
  fail_test "apply failed to re-deploy subscription page"
fi

if ssh root@"$EXIT_IP" "docker ps --filter name=remnawave-subscription-page --format '{{.Status}}'" 2>/dev/null | grep -q "Up"; then
  pass "subscription page container is running after re-enable"
else
  fail_test "subscription page container not running after re-enable apply"
fi

# Verify nginx route serves a response.
#
# The Remnawave subscription-page app routes by user short UUID
# and does NOT serve the root path — hitting `/` on the upstream leaves
# the app waiting for a client identifier and the connection eventually
# hangs. An earlier iteration of this test probed `/` and saw 502s not
# because the app was broken but because the request never got a status
# back. Probe a real per-user path instead: `/<sub_path>/<short_uuid>`.
SUB_PATH=$(python3 -c "
from meridian.cluster import ClusterConfig
c = ClusterConfig.load()
print(c.subscription_page.path if c.subscription_page else '')
")
DEFAULT_SHORT_UUID=$(python3 << 'PYEOF'
from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel

cluster = ClusterConfig.load()
with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
    user = panel.get_user("default")
    print(user.short_uuid if user else "")
PYEOF
)
if [ -n "$SUB_PATH" ] && [ -n "$DEFAULT_SHORT_UUID" ]; then
  PROBE_URL="https://$EXIT_IP/$SUB_PATH/$DEFAULT_SHORT_UUID"
  HTTP_CODE="000"
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    HTTP_CODE=$(curl -k -o /dev/null -s -w '%{http_code}' "$PROBE_URL" 2>/dev/null || echo "000")
    case "$HTTP_CODE" in
      2*|3*) break ;;
    esac
    sleep 2
  done
  case "$HTTP_CODE" in
    2*|3*)
      pass "subscription page nginx route returned HTTP $HTTP_CODE for /$SUB_PATH/<short_uuid>"
      ;;
    *)
      # Diagnostics: inspect the container directly so any future debugging
      # has the raw evidence instead of just the 502.
      echo "    DIAG: probed $PROBE_URL"
      echo "    DIAG: docker ps --filter name=remnawave-subscription-page:"
      ssh root@"$EXIT_IP" "docker ps --filter name=remnawave-subscription-page --format '{{.Status}} | {{.Ports}}'" 2>/dev/null | sed 's/^/      /'
      echo "    DIAG: last 30 lines of subscription-page logs:"
      ssh root@"$EXIT_IP" "docker logs --tail 30 remnawave-subscription-page 2>&1" | sed 's/^/      /' || true
      echo "    DIAG: host-side listen on 3020:"
      ssh root@"$EXIT_IP" "ss -ltnp 2>/dev/null | grep :3020 || echo '      (nothing listening on 3020)'" | sed 's/^/      /'
      echo "    DIAG: curl directly to 127.0.0.1:3020/$DEFAULT_SHORT_UUID on exit-node:"
      ssh root@"$EXIT_IP" "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3020/$DEFAULT_SHORT_UUID 2>&1" | sed 's/^/      /' || true
      fail_test "subscription page nginx route returned HTTP $HTTP_CODE after polling (expected 2xx/3xx)"
      ;;
  esac
else
  [ -z "$SUB_PATH" ] && fail_test "subscription page path not persisted after apply"
  [ -z "$DEFAULT_SHORT_UUID" ] && fail_test "could not resolve default user short_uuid"
fi

# ── Stage 10: Hardening verification ────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 10: Hardening verification"
echo "═══════════════════════════════════════"

# The stage 8 redeploy runs WITHOUT --no-harden, so hardening is applied.
# UFW filtering can't be tested end-to-end on a Docker bridge (all container
# ports are reachable regardless), but we verify the configuration is correct.

echo ">>> Checking firewall configuration..."
UFW_STATUS=$(ssh root@"$EXIT_IP" "ufw status" 2>/dev/null || true)

if echo "$UFW_STATUS" | grep -q "Status: active"; then
  pass "ufw firewall active"
else
  fail_test "ufw not active after hardened deploy"
fi

if echo "$UFW_STATUS" | grep -q "443/tcp"; then
  pass "ufw allows HTTPS (443/tcp)"
else
  fail_test "ufw missing HTTPS rule (443/tcp)"
fi

if echo "$UFW_STATUS" | grep -q "22/tcp"; then
  pass "ufw allows SSH (22/tcp)"
else
  fail_test "ufw missing SSH rule (22/tcp)"
fi

# Port 80 is opened because hosted_page is always enabled (setup.py hardcodes it)
if echo "$UFW_STATUS" | grep -q "80/tcp"; then
  pass "ufw allows HTTP (80/tcp) for hosted pages"
else
  fail_test "ufw missing HTTP rule (80/tcp)"
fi

# Verify no unexpected ports beyond 22, 80, 443 (and Docker internal ranges)
UNEXPECTED=$(echo "$UFW_STATUS" | grep "ALLOW" | grep -v "22/tcp" | grep -v "80/tcp" | grep -v "443/tcp" | grep -v "172\." || true)
if [ -n "$UNEXPECTED" ]; then
  fail_test "unexpected ufw rules: $UNEXPECTED"
else
  pass "no unexpected ufw rules"
fi

echo ">>> Checking SSH hardening..."
SSHD_CONFIG=$(ssh root@"$EXIT_IP" "sshd -T" 2>/dev/null || true)

if echo "$SSHD_CONFIG" | grep -q "passwordauthentication no"; then
  pass "SSH password authentication disabled"
else
  fail_test "SSH password authentication still enabled"
fi

echo ">>> Checking fail2ban..."
if ssh root@"$EXIT_IP" "systemctl is-active fail2ban" 2>/dev/null | grep -q "active"; then
  pass "fail2ban service active"
else
  fail_test "fail2ban not active after hardened deploy"
fi

# ── Stage 11: Teardown ───────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Stage 11: Teardown"
echo "═══════════════════════════════════════"

echo ">>> Removing relay..."
meridian relay remove "$RELAY_IP" --yes 2>&1 || echo "    WARN: relay remove failed (non-fatal)"

echo ">>> Tearing down exit node..."
meridian teardown "$EXIT_IP" --yes 2>&1 || echo "    WARN: teardown failed (non-fatal)"

# Verify port 443 freed
if ssh root@"$EXIT_IP" ss -tlnp sport = :443 2>/dev/null | grep -q LISTEN; then
  fail_test "port 443 still listening after teardown"
else
  pass "port 443 freed"
fi

# Verify Remnawave containers stopped (panel stack removal may be slow)
REMAINING=$(ssh root@"$EXIT_IP" docker ps --format '{{.Names}}' 2>/dev/null || true)
if echo "$REMAINING" | grep -q "^remnawave-node$"; then
  fail_test "remnawave-node still running after teardown"
else
  pass "remnawave-node stopped"
fi
# Panel backend containers (remnawave, remnawave-db, remnawave-redis) may take
# a few seconds to stop via docker compose down --rmi. Check but don't hard-fail.
if echo "$REMAINING" | grep -q "^remnawave$"; then
  echo "    WARN: remnawave panel backend still running (may need more time to stop)"
else
  pass "remnawave panel stopped"
fi

# ── Summary ──────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "═══════════════════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
