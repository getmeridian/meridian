#!/usr/bin/env bash
set -euo pipefail

export MERIDIAN_HOME=${MERIDIAN_HOME:-/tmp/meridian-home}
export PYTHONUNBUFFERED=1
export PYTHONPATH=/workspace${PYTHONPATH:+:$PYTHONPATH}

SCRIPT_DIR=${SYSTEMLAB_SCRIPT_DIR:-/workspace/tests/systemlab/scripts}
# Debugging: SYSTEMLAB_STAGES picks stages; SYSTEMLAB_KEEP=1 skips cleanup and keeps this
# container up so one stage can rerun with `docker compose exec controller bash .../stages/NN-*.sh`.
STAGES=${SYSTEMLAB_STAGES:-05-single-deploy.sh 10-deploy.sh 20-subscriptions.sh 25-golden.sh 30-resilience.sh 40-removal.sh}
KEEP=${SYSTEMLAB_KEEP:-}

keep_alive() {
  if [ -n "$KEEP" ]; then
    echo "  SYSTEMLAB_KEEP set: fleet left running; the controller waits for docker compose exec"
    sleep infinity
  fi
}

cleanup_nested() {
  local status=$?
  trap - EXIT
  set +e
  keep_alive
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

for stage in $STAGES; do
  run_stage "$stage"
done

trap - EXIT
keep_alive
bash "$SCRIPT_DIR/stages/90-cleanup.sh"

echo
echo "═══════════════════════════════════════"
echo "  V4 SYSTEM LAB PASSED"
echo "═══════════════════════════════════════"
