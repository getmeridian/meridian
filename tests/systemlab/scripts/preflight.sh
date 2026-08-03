#!/usr/bin/env bash
set -euo pipefail

if ! docker compose version >/dev/null 2>&1; then
  cat >&2 <<'EOF'
System lab requires the Docker Compose CLI plugin.
Point DOCKER_CONFIG at a config containing cliPluginsExtraDirs, or install the
Compose plugin for the active Docker CLI.
EOF
  exit 2
fi

if ! docker buildx version >/dev/null 2>&1; then
  echo "System lab requires the Docker Buildx CLI plugin." >&2
  exit 2
fi

if ! docker info >/dev/null 2>&1; then
  echo "System lab cannot reach the selected Docker daemon." >&2
  exit 2
fi

if ! docker compose -f tests/systemlab/compose.yml config >/dev/null; then
  echo "System lab Compose configuration is invalid." >&2
  exit 2
fi

check_daemon_registry() {
  docker pull --quiet ubuntu:24.04 >/dev/null 2>&1
}

repair_colima_dns() {
  local socket_path profile_path profile
  socket_path=${DOCKER_HOST:-}
  profile_path=${socket_path#*/.colima/}
  if [ "$profile_path" = "$socket_path" ]; then
    return 1
  fi
  profile=${profile_path%%/*}
  if [ "$profile" != "meridian" ] || ! command -v colima >/dev/null 2>&1; then
    return 1
  fi
  echo "Repairing DNS inside Colima profile '$profile'..." >&2
  colima ssh -p "$profile" -- sudo sh -c \
    'rm -f /etc/resolv.conf && printf "nameserver 192.168.5.2\nnameserver 1.1.1.1\n" > /etc/resolv.conf'
}

# Registry pulls use the Colima guest resolver, which is distinct from DNS in
# ordinary bridge containers. Colima can leave /etc/resolv.conf pointing at a
# stopped loopback resolver after restart; repair the selected profile once.
if ! check_daemon_registry; then
  if ! repair_colima_dns || ! check_daemon_registry; then
    cat >&2 <<'EOF'
The Docker daemon cannot reach Docker Hub for required base images.

For the Meridian Colima profile, restart with explicit resolvers:
  colima stop meridian
  colima start meridian --dns 192.168.5.2 --dns 1.1.1.1
EOF
    exit 2
  fi
fi

dns_hosts=(
  registry-1.docker.io
  ports.ubuntu.com
  azure.archive.ubuntu.com
  deb.debian.org
  nginx.org
  download.docker.com
  pypi.org
  ghcr.io
)
dns_list=$(IFS=,; printf '%s' "${dns_hosts[*]}")
if ! docker run --rm --network bridge ubuntu:24.04 sh -c '
  for host in "$@"; do
    timeout 10 getent ahosts "$host" >/dev/null || exit 1
  done
' sh "${dns_hosts[@]}"; then
  cat >&2 <<EOF
Docker containers cannot resolve the package mirrors required by the system lab:
  ${dns_list}

For the Meridian Colima profile, restart with explicit resolvers:
  colima stop meridian
  colima start meridian --dns 192.168.5.2 --dns 1.1.1.1
EOF
  exit 2
fi

echo "System lab preflight passed (Compose, daemon registry access, and container DNS)."
