#!/usr/bin/env bash
set -euo pipefail

export MERIDIAN_HOME=${MERIDIAN_HOME:-/tmp/meridian-home}
export PYTHONUNBUFFERED=1
export PYTHONPATH=/workspace${PYTHONPATH:+:$PYTHONPATH}

SCRIPT_DIR=/workspace/tests/systemlab/scripts

for stage in \
  00-bootstrap.sh \
  10-deploy.sh \
  20-subscriptions.sh \
  30-resilience.sh
do
  echo
  echo "═══════════════════════════════════════"
  echo "  ${stage%.sh}"
  echo "═══════════════════════════════════════"
  bash "$SCRIPT_DIR/stages/$stage"
done

echo
echo "═══════════════════════════════════════"
echo "  V4 SYSTEM LAB PASSED"
echo "═══════════════════════════════════════"
