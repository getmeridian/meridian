#!/usr/bin/env bash
set -euo pipefail

export MERIDIAN_HOME=${MERIDIAN_HOME:-/tmp/meridian-home}
export PYTHONUNBUFFERED=1
export PYTHONPATH=/workspace${PYTHONPATH:+:$PYTHONPATH}

SCRIPT_DIR=${SYSTEMLAB_SCRIPT_DIR:-/workspace/tests/systemlab/scripts}

cleanup_nested() {
  local status=$?
  trap - EXIT
  set +e
  bash "$SCRIPT_DIR/stages/90-cleanup.sh"
  local cleanup_status=$?
  if [ "$status" -ne 0 ]; then
    exit "$status"
  fi
  exit "$cleanup_status"
}
trap cleanup_nested EXIT

run_stage() {
  local stage=$1
  echo
  echo "═══════════════════════════════════════"
  echo "  ${stage%.sh}"
  echo "═══════════════════════════════════════"
  bash "$SCRIPT_DIR/stages/$stage"
}

run_stage 00-bootstrap.sh
bash "$SCRIPT_DIR/stages/90-cleanup.sh"

for stage in \
  10-deploy.sh \
  20-subscriptions.sh \
  30-resilience.sh
do
  run_stage "$stage"
done

bash "$SCRIPT_DIR/stages/90-cleanup.sh"
trap - EXIT

echo
echo "═══════════════════════════════════════"
echo "  V4 SYSTEM LAB PASSED"
echo "═══════════════════════════════════════"
