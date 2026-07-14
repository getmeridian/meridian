#!/usr/bin/env bash
# Generate SSH keypair and Pebble CA for system lab.
# Run before `docker compose build` if fixtures don't exist.
set -euo pipefail

DIR="$(dirname "$0")/../fixtures"
mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"

# SSH keypair (test-only, never committed)
if [ ! -f "$DIR/id_ed25519" ]; then
  ssh-keygen -t ed25519 -N '' -f "$DIR/id_ed25519" -q
  cp "$DIR/id_ed25519.pub" "$DIR/controller_authorized_keys"
  echo "Generated SSH keypair in $DIR"
fi

# Pebble root CA (extracted from Docker image)
if [ ! -f "$DIR/pebble-ca.pem" ]; then
  CID=$(docker create ghcr.io/letsencrypt/pebble:2.10.0 2>/dev/null) || true
  if [ -n "$CID" ]; then
    docker cp "$CID:/test/certs/pebble.minica.pem" "$DIR/pebble-ca.pem" 2>/dev/null
    docker rm "$CID" >/dev/null 2>&1
    echo "Extracted Pebble root CA to $DIR/pebble-ca.pem"
  else
    echo "WARN: Could not extract Pebble CA (docker not available?)"
  fi
fi
