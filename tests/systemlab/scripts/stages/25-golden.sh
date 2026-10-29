#!/usr/bin/env bash
# Replay the golden corpus against the full fleet: every recorded client User-Agent for the
# active client, a suspended client, and an unknown subscription must get the answer the
# corpus recorded from Remnawave, except the deviations tests/test_control_subscription_corpus.py names.
set -euo pipefail

source /workspace/tests/systemlab/scripts/stages/common.sh

SUBSCRIPTION_TEST=$SYSTEMLAB_ROOT/scripts/test-subscription.py

python3 "$GOLDEN_DIFF" --topology fleet --user acceptance --unknown
pass "every recorded client gets the golden corpus answer for the active client"

meridian client disable acceptance
python3 "$GOLDEN_DIFF" --topology fleet --user acceptance --state disabled
meridian client enable acceptance
pass "every recorded client gets the golden corpus answer for a suspended client"

wait_for_command "re-enabled client to connect again" 90 python3 "$SUBSCRIPTION_TEST" --automatic
pass "re-enabled client connects again"
