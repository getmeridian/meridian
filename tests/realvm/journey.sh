#!/usr/bin/env bash
# Beta journey on fresh lab VMs with the working tree: what a tester does, in order.
#
#   python -m tests.realvm.lab up ctl:hel1 exit:fsn1:debian-13 relay:nbg1:ubuntu-22.04
#   source ~/.cache/meridian-realvm/lab-<id>/env.sh
#   tests/realvm/journey.sh "$VM_CTL" "$VM_EXIT" "$VM_RELAY"
#
# EXIT and RELAY are optional. Every step prints its exit code; the summary
# lists unexpected ones and the script exits 1 if there were any.
set -uo pipefail

CTL=${1:?usage: journey.sh CONTROL_IP [EXIT_IP [RELAY_IP]]}
EXIT=${2:-}
RELAY=${3:-}
: "${REALVM_LAB:?source the lab env.sh first}"
cd "$(dirname "$0")/../.."
M=(uv run meridian)
FAILED=()

step() {  # step NAME EXPECTED_RC COMMAND...
  local name=$1 expected=$2
  shift 2
  echo
  echo "=== $name ($(date +%T))"
  "$@"
  local rc=$?
  echo "rc=$rc"
  [ "$rc" -eq "$expected" ] || FAILED+=("$name: rc=$rc, expected $expected")
}

on() {  # on IP COMMAND — run on a lab VM
  local ip=$1
  shift
  ssh "root@$ip" "$@"
}

step deploy 0 "${M[@]}" deploy "$CTL" --yes
if [ -n "$EXIT" ]; then
  step node-add 0 "${M[@]}" node add "$EXIT" --name astra --yes
fi
if [ -n "$EXIT" ] && [ -n "$RELAY" ]; then
  step relay-add 0 "${M[@]}" relay add "$RELAY" --exit astra --name boba --yes
fi
step node-list 0 "${M[@]}" node list
step client-add 0 "${M[@]}" client add bob --yes
step client-show 0 "${M[@]}" client show bob
step test 0 "${M[@]}" test
step check 0 "${M[@]}" check
step apply-converged 0 "${M[@]}" apply --yes
step plan-converged 0 "${M[@]}" plan
step doctor 0 "${M[@]}" doctor "$CTL"  # bare doctor asks which server once several are saved

if [ -n "$EXIT" ] && [ -n "$RELAY" ]; then
  step relay-remove 0 "${M[@]}" relay remove boba --yes
  step relay-clean 0 on "$RELAY" '! systemctl list-units --all --no-legend "netfwd-*" | grep -q .'
fi
if [ -n "$EXIT" ]; then
  step node-remove 0 "${M[@]}" node remove astra --yes
  # The removed exit must stop renewing its certificate and drop the challenge listener.
  step exit-clean 0 on "$EXIT" '! ls /root/.acme.sh/*/*.conf 2>/dev/null | grep -v account.conf | grep -q . && ! ls /etc/nginx/sites-enabled/ 2>/dev/null | grep -q acme'
  step test-after-removal 0 "${M[@]}" test
fi

echo
echo "=== done ($(date +%T))"
if [ ${#FAILED[@]} -gt 0 ]; then
  printf '  ✗ %s\n' "${FAILED[@]}"
  exit 1
fi
echo "  ✓ every step returned what it should"
