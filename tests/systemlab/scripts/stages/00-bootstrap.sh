#!/usr/bin/env bash
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

mkdir -p "$MERIDIAN_HOME" /root/.ssh

for host in $(all_server_ips); do
  acquired=0
  for _ in $(seq 1 60); do
    if ssh-keyscan -T 2 "$host" \
      >>/root/.ssh/known_hosts 2>/dev/null
    then
      acquired=1
      break
    fi
    sleep 2
  done
  if [ "$acquired" -ne 1 ]; then
    echo "SSH host key unavailable for $host" >&2
    exit 1
  fi
done
pass "SSH host keys acquired"

cp "$SYSTEMLAB_ROOT/fixtures/id_ed25519" \
  /root/.ssh/id_ed25519
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

for host in $(all_server_ips); do
  ready=0
  for _ in $(seq 1 40); do
    state=$(ssh root@"$host" \
      systemctl is-system-running 2>/dev/null || true)
    if [ "$state" = "running" ] || \
      [ "$state" = "degraded" ]
    then
      ready=1
      break
    fi
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then
    echo "systemd unavailable on $host" >&2
    exit 1
  fi
done
pass "all topology servers booted"

meridian server add "$EXIT_A_IP" --name exit-a
meridian server add "$EXIT_B_IP" --name exit-b
meridian server add "$RELAY_A_IP" --name relay-a
meridian server add "$RELAY_B_IP" --name relay-b
meridian server add "$GATEWAY_IP" --name gateway-a
pass "immutable server shelf registered"

python3 "$SYSTEMLAB_ROOT/scripts/write-intent.py" \
  "$INTENT_PATH"
python3 -m json.tool "$INTENT_PATH" >/dev/null
pass "complete V4 intent generated"
