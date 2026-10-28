#!/usr/bin/env bash
# Record the full fleet's subscriptions for the golden corpus: every client User-Agent for
# the active client, a suspended client, and an unknown subscription.
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

SUBSCRIPTION_TEST=$SYSTEMLAB_ROOT/scripts/test-subscription.py

python3 "$GOLDEN_CAPTURE" --topology fleet --user acceptance --unknown
pass "golden subscription corpus captured for the active client"

meridian client disable acceptance
python3 "$GOLDEN_CAPTURE" --topology fleet --user acceptance --state disabled
meridian client enable acceptance
pass "golden subscription corpus captured for a suspended client"

wait_for_command "re-enabled client to connect again" 90 python3 "$SUBSCRIPTION_TEST" --automatic
pass "re-enabled client connects again"
