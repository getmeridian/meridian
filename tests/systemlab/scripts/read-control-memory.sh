#!/usr/bin/env bash
# Run on the control server over SSH; all figures belong to its service cgroup.
set -euo pipefail

expected_invocation=${1:-}
cgroup_root=${SYSTEMLAB_CGROUP_ROOT:-/sys/fs/cgroup}

fail() {
  echo "Control service $1" >&2
  exit 1
}

check_invocation() {
  if [ "$(systemctl show ctld -p ActiveState --value)" != "active" ]; then
    fail "is not active after the memory workload."
  fi
  if [ -z "$expected_invocation" ] || \
    [ "$(systemctl show ctld -p InvocationID --value)" != "$expected_invocation" ]; then
    fail "restarted during the memory workload; its new peak cannot prove the budget."
  fi
}

has_peak() {
  case "$1" in
    ''|*[!0-9]*|0|18446744073709551615) return 1 ;;
    *) return 0 ;;
  esac
}

check_invocation
if [ "$(systemctl show ctld -p MemoryAccounting --value)" != "yes" ]; then
  fail "memory accounting is disabled; System Lab requires the cgroup memory controller."
fi

peak=$(systemctl show ctld -p MemoryPeak --value 2>/dev/null || true)
if ! has_peak "$peak"; then
  cgroup=$(systemctl show ctld -p ControlGroup --value)
  peak=''
  if [ -n "$cgroup" ] && [ -r "$cgroup_root$cgroup/memory.peak" ]; then
    read -r peak < "$cgroup_root$cgroup/memory.peak" || true
  fi
fi
if ! has_peak "$peak"; then
  fail "memory peak is unavailable; System Lab requires a writable cgroup v2 memory controller."
fi
maximum=$(systemctl show ctld -p MemoryMax --value)
case "$maximum" in
  ''|*[!0-9]*|0|18446744073709551615) fail "MemoryMax is unavailable or unlimited." ;;
esac
check_invocation
printf '%s %s\n' "$peak" "$maximum"
