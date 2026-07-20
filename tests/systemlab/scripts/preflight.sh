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

# BuildKit resolves the external Dockerfile frontend through the daemon host,
# which is a different DNS path from ordinary bridge containers.
if ! buildkit_output=$(
  docker buildx build \
    --quiet \
    --file - . 2>&1 <<'EOF'
# syntax=docker/dockerfile:1
FROM scratch
EOF
); then
  printf '%s\n' "$buildkit_output" >&2
  cat >&2 <<'EOF'
Docker BuildKit cannot load the external Dockerfile frontend.

If the output above reports a DNS lookup on [::1]:53, configure explicit
resolvers and repair the Meridian Colima guest's dangling /etc/resolv.conf:
  colima stop meridian
  colima start meridian --dns 192.168.5.2 --dns 1.1.1.1
  colima ssh -p meridian -- sudo sh -c 'rm -f /etc/resolv.conf && printf "nameserver 192.168.5.2\nnameserver 1.1.1.1\n" > /etc/resolv.conf'
EOF
  exit 2
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

echo "System lab preflight passed (Compose, daemon, BuildKit, and container DNS)."
