#!/usr/bin/env bash
# Install a published meridian-vpn on a lab VM and deploy it there, as a tester would.
#
#   tests/realvm/deploy_published.sh "$VM_EXIT" 4.0.0b8 --domain "$(echo "$VM_EXIT" | tr . -).sslip.io"
#
# Runs `meridian deploy local ARGS... --yes` on the server under `script`, so the
# full transcript stays in /root/deploy.log there. Exits with the deploy's exit code.
set -euo pipefail

IP=${1:?usage: deploy_published.sh IP VERSION [DEPLOY_ARGS...]}
VERSION=${2:?usage: deploy_published.sh IP VERSION [DEPLOY_ARGS...]}
shift 2
ARGS=""
# printf with no arguments still prints its format once, which would deploy with a stray ''.
(($#)) && ARGS=$(printf '%q ' "$@")

# ssh joins its arguments into one remote command line, so quote them for that shell.
ssh -o BatchMode=yes "root@$IP" "bash -s -- $(printf '%q ' "$VERSION" "$ARGS")" <<'REMOTE'
set -euo pipefail
version=$1 args=${2:-}
export PATH=/root/.local/bin:$PATH
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
uv tool install -q --force "meridian-vpn==$version"
meridian --version
rc=0
script -qec "meridian deploy local $args --yes" /root/deploy.log </dev/null || rc=$?
echo "deploy exit: $rc"
exit "$rc"
REMOTE
