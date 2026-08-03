#!/usr/bin/env bash
# Generate the SSH keypair and Pebble endpoint CA for System Lab.
# Run before `docker compose build` if fixtures don't exist.
set -euo pipefail

DIR="${SYSTEMLAB_FIXTURES_DIR:-$(dirname "$0")/../fixtures}"
mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"

# SSH keypair (test-only, never committed)
if [ ! -f "$DIR/id_ed25519" ]; then
  ssh-keygen -t ed25519 -N '' -f "$DIR/id_ed25519" -q
  echo "Generated SSH keypair in $DIR"
fi
chmod 0600 "$DIR/id_ed25519"
PUBLIC_KEY=$(ssh-keygen -y -f "$DIR/id_ed25519")
printf '%s\n' "$PUBLIC_KEY" > "$DIR/id_ed25519.pub"
cp "$DIR/id_ed25519.pub" "$DIR/controller_authorized_keys"
chmod 0644 "$DIR/id_ed25519.pub" "$DIR/controller_authorized_keys"

# This static CA authenticates Pebble's HTTPS endpoint. Pebble 2.10 generates
# its certificate-issuance root at runtime; 00-bootstrap installs that root.
PEBBLE_IMAGE=ghcr.io/letsencrypt/pebble:2.10.0
PEBBLE_CA="$DIR/pebble-ca.pem"
PEBBLE_TMP=$(mktemp -d "$DIR/.pebble-ca.XXXXXX")
CID=""

cleanup() {
  if [ -n "$CID" ]; then
    docker rm "$CID" >/dev/null 2>&1 || true
  fi
  rm -rf "$PEBBLE_TMP"
}
trap cleanup EXIT

if ! CID=$(docker create "$PEBBLE_IMAGE" 2>/dev/null); then
  echo "ERROR: Could not create the pinned Pebble image to extract its root CA." >&2
  exit 1
fi

CANDIDATE_CA="$PEBBLE_TMP/pebble-ca.pem"
if ! docker cp "$CID:/test/certs/pebble.minica.pem" "$CANDIDATE_CA" >/dev/null 2>&1 \
  || [ ! -s "$CANDIDATE_CA" ]; then
  echo "ERROR: Could not extract the root CA from $PEBBLE_IMAGE." >&2
  exit 1
fi

if [ ! -f "$PEBBLE_CA" ] || ! cmp -s "$CANDIDATE_CA" "$PEBBLE_CA"; then
  mv "$CANDIDATE_CA" "$PEBBLE_CA"
  chmod 0644 "$PEBBLE_CA"
  echo "Refreshed Pebble root CA at $PEBBLE_CA"
fi
