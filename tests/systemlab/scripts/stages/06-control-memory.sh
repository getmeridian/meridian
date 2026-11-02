#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=tests/systemlab/scripts/stages/common.sh
source /workspace/tests/systemlab/scripts/stages/common.sh

invocation=$(ssh root@"$EXIT_B_IP" systemctl show ctld -p InvocationID --value)
if [ -z "$invocation" ]; then
  echo "Control service invocation is unavailable; the memory workload cannot prove a peak." >&2
  exit 1
fi
workload_status=0
python3 -m tests.systemlab.control_memory exercise || workload_status=$?
memory=$(ssh root@"$EXIT_B_IP" bash -s -- "$invocation" < "$SYSTEMLAB_ROOT/scripts/read-control-memory.sh")
read -r peak maximum <<< "$memory"
python3 -m tests.systemlab.control_memory check "$peak" "$maximum"
if [ "$workload_status" -ne 0 ]; then
  exit "$workload_status"
fi
pass "control service stayed within its memory budget through expensive reads"
