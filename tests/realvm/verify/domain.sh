#!/usr/bin/env bash
# Tier α verification for topology=domain (#110): a panel on its own domain beside an exit on the IP.
# Runs on the developer's machine. Requires TARGET_IP env var; the orchestrator sets MERIDIAN_HOME.
#
# Optional: REALVM_PANEL_DOMAIN (your own name, pointed at TARGET_IP; default is the sslip.io name),
#           REALVM_DNS_WAIT (seconds to wait for the name to resolve to TARGET_IP, default 120).
#
# Exits 0 on all-pass, 1 on any failed claim, 2 when the run cannot continue, and 75 when Let's
# Encrypt's rate limit refused issuance (inconclusive: sslip.io names share limits with the world).
set -uo pipefail

: "${TARGET_IP:?TARGET_IP env var required}"
INCONCLUSIVE=75
cd "$(dirname "$0")/../../.."
export MERIDIAN_DISABLE_UPDATE_CHECK=1
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
if [ -z "${MERIDIAN_HOME:-}" ]; then
  # Never touch the developer's own fleet.
  export MERIDIAN_HOME="$WORK/home"
  mkdir -p "$MERIDIAN_HOME"
fi

M=(uv run meridian)
H=(uv run python -m tests.realvm.domain_support)
PASS=0
FAIL=0
pass()      { PASS=$((PASS+1)); echo "    ✓ $1"; }
fail_test() { FAIL=$((FAIL+1)); echo "    ✗ FAIL: $1"; }
on()        { ssh -o StrictHostKeyChecking=no "root@$TARGET_IP" "$@"; }

inconclusive_if_rate_limited() {  # LOGFILE
  if "${H[@]}" rate-limited < "$1"; then
    echo ""
    echo "  ? INCONCLUSIVE: Let's Encrypt refused to issue (rateLimited). sslip.io is not on the Public"
    echo "    Suffix List, so its names share per-domain limits with everyone. This says nothing about"
    echo "    Meridian. Retry later, or set REALVM_PANEL_DOMAIN to a name you own."
    exit "$INCONCLUSIVE"
  fi
}

run_logged() {  # NAME LOGFILE COMMAND... — stream the output, keep it, and stop on a rate limit
  local name=$1 log=$2
  shift 2
  echo ">>> $name ..."
  "$@" 2>&1 | tee "$log"
  local rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] || inconclusive_if_rate_limited "$log"
  return "$rc"
}

retry() {  # COMMAND... — a fresh apply can take a few seconds to read as converged
  local attempt
  for attempt in 1 2 3; do
    "$@" && return 0
    [ "$attempt" -lt 3 ] && sleep 10
  done
  return 1
}

cert_san() {  # [SNI] — subjectAltName of what 443 serves on the server, with or without an SNI
  local sni_flag="-noservername"
  [ -n "${1:-}" ] && sni_flag="-servername $1"
  on "openssl s_client -connect 127.0.0.1:443 $sni_flag </dev/null 2>/dev/null | openssl x509 -noout -ext subjectAltName" 2>/dev/null || true
}

acme_values() {  # KEY — one acme.sh setting from every certificate entry on the server
  on "grep -h '^$1=' /root/.acme.sh/*/*.conf 2>/dev/null" 2>/dev/null || true
}

test_has_no_failures() {
  local out counts passed failed warnings skipped
  out=$("${M[@]}" --json test 2>/dev/null) || true
  counts=$("${H[@]}" counts <<<"$out" 2>/dev/null) || { echo "    meridian test produced no verdict"; return 1; }
  read -r passed failed warnings skipped <<<"$counts"
  echo "    test: passed=$passed failed=$failed warnings=$warnings skipped=$skipped"
  [ "$failed" -eq 0 ] && [ "$passed" -gt 0 ]
}

check_passes() { "${M[@]}" check >"$WORK/check.log" 2>&1 || { tail -15 "$WORK/check.log"; return 1; }; }
plan_converged() { "${M[@]}" plan >"$WORK/plan.log" 2>&1 || { tail -15 "$WORK/plan.log"; return 1; }; }

PANEL=$("${H[@]}" panel-domain "$TARGET_IP") || { echo "  ✗ cannot name the panel domain"; exit 2; }

echo "═══════════════════════════════════════"
echo "  Real-VM tier α verify (domain) — $TARGET_IP"
echo "  Panel domain: $PANEL"
echo "═══════════════════════════════════════"

# ── 1. SSH reachability + host key scan ─────────────────
echo ">>> SSH reachability + host key scan..."
if ssh-keyscan -T 15 "$TARGET_IP" >> "${REALVM_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}" 2>/dev/null; then
  pass "SSH reachable, host key captured"
else
  fail_test "Cannot reach SSH on $TARGET_IP"
  exit 2
fi

# ── 2. The panel's name must already point at the VM ─────────────────
echo ">>> Waiting for $PANEL to resolve to $TARGET_IP ..."
if python3 -c '
import socket, sys, time
name, ip, wait = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.time() + wait
while True:
    try:
        if ip in socket.gethostbyname_ex(name)[2]:
            sys.exit(0)
    except OSError:
        pass
    if time.time() >= deadline:
        sys.exit(1)
    time.sleep(5)
' "$PANEL" "$TARGET_IP" "${REALVM_DNS_WAIT:-120}"; then
  pass "$PANEL resolves to the VM"
else
  fail_test "$PANEL does not resolve to $TARGET_IP — point its A record at the VM and re-run"
  exit 2
fi

# ── 3. Save the server and build the intent: panel on the domain, exit paths on the IP ─────────────────
echo ">>> meridian server add root@$TARGET_IP ..."
if "${M[@]}" server add "root@$TARGET_IP"; then
  pass "server saved"
else
  fail_test "meridian server add failed"
  exit 2
fi
SERVER_REF=$(python3 -c '
import json, os, sys
servers = json.load(open(os.path.join(os.environ["MERIDIAN_HOME"], "servers.json")))["servers"]
print(next(s["id"] for s in servers if s["host"] == sys.argv[1]))
' "$TARGET_IP" 2>/dev/null) || { fail_test "no saved server id for $TARGET_IP in servers.json"; exit 2; }
echo "    server id: $SERVER_REF"
"${H[@]}" intent "$SERVER_REF" "$TARGET_IP" "$PANEL" > "$WORK/intent.json" || { fail_test "could not build the intent"; exit 2; }

if run_logged "meridian setup --intent (panel on $PANEL, exit paths on the IP)" "$WORK/setup.log" \
    "${M[@]}" setup --intent "$WORK/intent.json" --yes; then
  pass "setup applied"
else
  fail_test "meridian setup failed — check output above"
  exit 2
fi

# VERIFY: panel_certificate_names_domain
# ── 4. The panel's certificate names its domain ─────────────────
echo ">>> 443 with SNI $PANEL ..."
SAN=$(cert_san "$PANEL")
echo "    $(echo "$SAN" | tr -s ' \n' ' ')"
if grep -qF "DNS:$PANEL" <<<"$SAN"; then
  pass "certificate for SNI $PANEL names the domain"
else
  fail_test "certificate for SNI $PANEL does not name it (the IP certificate overwrote the panel's?)"
fi

# VERIFY: ip_certificate_without_sni
# ── 5. Without SNI, the server's IP certificate ─────────────────
echo ">>> 443 without SNI ..."
SAN=$(cert_san)
echo "    $(echo "$SAN" | tr -s ' \n' ' ')"
if grep -qF "IP Address:$TARGET_IP" <<<"$SAN"; then
  pass "certificate without SNI names the server's IP"
else
  fail_test "certificate without SNI does not name $TARGET_IP (the panel's certificate overwrote it?)"
fi

# VERIFY: acme_entries_use_separate_files
# ── 6. Two acme.sh entries, two installed files ─────────────────
echo ">>> acme.sh certificate entries ..."
PATHS=$(acme_values Le_RealFullChainPath)
echo "$PATHS" | sed 's/^/    /'
ENTRY_COUNT=$(grep -c . <<<"$PATHS")
DISTINCT_COUNT=$(grep . <<<"$PATHS" | sort -u | wc -l | tr -d ' ')
if [ "$ENTRY_COUNT" -eq 2 ] && [ "$DISTINCT_COUNT" -eq 2 ]; then
  pass "two acme.sh entries install into two different files"
else
  fail_test "expected 2 entries with 2 distinct Le_RealFullChainPath, found $ENTRY_COUNT entries and $DISTINCT_COUNT distinct"
fi

# VERIFY: connection_test_ip_paths
# ── 7. Health with exit paths on the IP ─────────────────
echo ">>> meridian test (exit paths on the IP) ..."
if retry test_has_no_failures; then
  pass "meridian test has no failed checks"
else
  fail_test "meridian test reports failed checks"
fi

# VERIFY: health_check_ip_paths
echo ">>> meridian check ..."
if retry check_passes; then
  pass "meridian check passes"
else
  fail_test "meridian check did not pass"
fi

# VERIFY: plan_converged_ip_paths
echo ">>> meridian plan ..."
if retry plan_converged; then
  pass "plan is converged (exit 0)"
else
  fail_test "plan is not converged"
fi

# VERIFY: exit_paths_moved_to_domain
# ── 8. Move XHTTP and Hysteria2 onto the panel's domain ─────────────────
echo ">>> meridian fleet export, tls_sni/host $TARGET_IP -> $PANEL ..."
MOVED=0
if "${M[@]}" fleet export > "$WORK/fleet.yml" 2>/dev/null \
    && "${H[@]}" switch "$WORK/fleet.yml" "$TARGET_IP" "$PANEL"; then
  if run_logged "meridian fleet import --yes" "$WORK/import.log" "${M[@]}" fleet import "$WORK/fleet.yml" --yes; then
    MOVED=1
    pass "exit paths moved onto $PANEL"
  else
    fail_test "meridian fleet import failed"
  fi
else
  fail_test "could not export and edit the fleet file"
fi

# VERIFY: panel_certificate_survives_move
echo ">>> 443 with SNI $PANEL after the move ..."
SAN=$(cert_san "$PANEL")
echo "    $(echo "$SAN" | tr -s ' \n' ' ')"
if grep -qF "DNS:$PANEL" <<<"$SAN"; then
  pass "panel still serves its domain certificate"
else
  fail_test "certificate for SNI $PANEL no longer names the domain"
fi

# VERIFY: connection_test_domain_paths
# ── 9. Health with exit paths on the domain ─────────────────
echo ">>> meridian test (exit paths on the domain) ..."
if [ "$MOVED" -eq 1 ] && retry test_has_no_failures; then
  pass "meridian test has no failed checks"
else
  fail_test "meridian test reports failed checks after the move"
fi

# VERIFY: health_check_domain_paths
echo ">>> meridian check ..."
if [ "$MOVED" -eq 1 ] && retry check_passes; then
  pass "meridian check passes"
else
  fail_test "meridian check did not pass after the move"
fi

# VERIFY: plan_converged_domain_paths
echo ">>> meridian plan ..."
if [ "$MOVED" -eq 1 ] && retry plan_converged; then
  pass "plan is converged (exit 0)"
else
  fail_test "plan is not converged after the move"
fi

# VERIFY: ip_certificate_retired
# ── 10. The unused IP certificate is retired ─────────────────
echo ">>> acme.sh entries after the move ..."
DOMAINS=$(acme_values Le_Domain)
echo "$DOMAINS" | sed 's/^/    /'
if [ "$MOVED" -eq 1 ] && grep -qF "$PANEL" <<<"$DOMAINS" && ! grep -qF "$TARGET_IP" <<<"$DOMAINS"; then
  pass "the IP certificate's acme.sh entry is gone; the panel's remains"
else
  fail_test "expected only the panel's acme.sh entry after the move"
fi

# ── 11. Summary ─────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  PASS: $PASS   FAIL: $FAIL"
echo "═══════════════════════════════════════"

[ "$FAIL" -eq 0 ] || exit 1
