"""Pre-deploy compatibility checks run over SSH (or locally) on a candidate server."""

from __future__ import annotations

import ipaddress
import re
import shlex
import time
from collections.abc import Callable

from meridian.core.errors import MeridianError
from meridian.core.execution import ServerConnection
from meridian.core.verification import VerificationCheck, VerificationFinding
from meridian.diagnostics.checks import command_evidence_unavailable
from meridian.diagnostics.evidence import finding, finish_check
from meridian.facts import ServerFacts
from meridian.health import tcp_connect

Progress = Callable[[str], None]

_FIREWALL_HINT = "Allow inbound TCP 443 in your cloud provider's firewall / security group."
# Listeners Meridian itself runs on 443; a v4 stream artifact proves the server is ours.
_MERIDIAN_LISTENERS = frozenset({"nginx", "xray", "remnawave", "remnawave-node", "docker-proxy"})
_V4_STREAM_MARKER = 'test -n "$(ls /etc/nginx/stream.d/meridian-v4-*.conf 2>/dev/null)" && echo managed'


def collect_preflight(
    conn: ServerConnection,
    *,
    host: str,
    probe_ip: str,
    sni: str,
    domain: str = "",
    local_vantage: bool = False,
    progress: Progress | None = None,
) -> list[VerificationCheck]:
    """Check whether the server can run a Reality proxy; never mutates it beyond a 10-second listener."""
    note = progress or (lambda _label: None)
    facts = ServerFacts(conn)
    checks: list[VerificationCheck] = []

    note(f"camouflage target {sni}")
    checks.append(_camouflage_reachable(conn, sni))
    checks.append(_camouflage_resolves(conn, sni))

    note("port 443")
    port_check, port_free, port_inspected = _port_443_free(conn)
    checks.append(port_check)
    note("port 443 from outside")
    checks.append(_port_443_external(conn, probe_ip, port_free, port_inspected, local_vantage))

    if domain:
        note(f"domain {domain}")
        checks.append(_domain_dns(conn, domain, host, probe_ip))

    note("server OS and disk")
    checks.append(_server_os(facts))
    checks.append(_disk_space(facts))
    note("clock")
    checks.append(_clock_sync(conn, host))
    return checks


def _camouflage_reachable(conn: ServerConnection, sni: str) -> VerificationCheck:
    started = time.monotonic()
    q_sni = shlex.quote(sni)
    q_endpoint = shlex.quote(f"{sni}:443")
    first = conn.run(
        "command -v openssl >/dev/null 2>&1 || exit 127; "
        f"timeout 5 openssl s_client -connect {q_endpoint} -servername {q_sni} </dev/null 2>/dev/null | head -1",
        timeout=10,
    )
    output = first.stdout.strip()
    second = None
    if not output:
        # Plain TCP through bash, which minimal images keep (nc is often absent).
        q_probe = shlex.quote(f"exec 3<>/dev/tcp/{sni}/443")
        second = conn.run(f"timeout 3 bash -c {q_probe} 2>/dev/null && echo OK", timeout=8)
        output = second.stdout.strip()
    attempts = [result for result in (first, second) if result is not None]
    if any(marker in output for marker in ("CONNECTED", "OK", "Certificate")):
        found = finding("camouflage_reachable", "passed", f"{sni} is reachable from the server")
    elif any(result.returncode in {124, 255} for result in attempts) or all(
        result.returncode == 127 for result in attempts
    ):
        found = finding(
            "camouflage_reachable",
            "skipped",
            "Could not test camouflage target reachability (openssl and bash TCP probes are unavailable)",
        )
    else:
        found = finding(
            "camouflage_reachable",
            "failed",
            f"{sni} is NOT reachable from the server",
            "Pick another camouflage target: meridian scan SERVER",
        )
    return finish_check("camouflage_reachable", "Camouflage target", [found], started)


def _camouflage_resolves(conn: ServerConnection, sni: str) -> VerificationCheck:
    started = time.monotonic()
    # The server's own resolver, so no third party learns the target.
    result = conn.run(
        "command -v getent >/dev/null 2>&1 || exit 127; "
        f"getent ahostsv4 {shlex.quote(sni)} 2>/dev/null | awk 'NR == 1 {{print $1}}'",
        timeout=10,
    )
    address = result.stdout.strip()
    if address:
        found = finding("camouflage_dns", "passed", f"{sni} resolves from the server ({address})")
    elif command_evidence_unavailable(result.returncode):
        found = finding("camouflage_dns", "skipped", "Could not test camouflage target DNS through the server resolver")
    else:
        found = finding(
            "camouflage_dns",
            "failed",
            f"{sni} did not resolve through the server's configured DNS",
            "Fix the server's DNS resolver or pick another camouflage target.",
        )
    return finish_check("camouflage_dns", "Camouflage target DNS", [found], started)


def _port_443_free(conn: ServerConnection) -> tuple[VerificationCheck, bool, bool]:
    """Return the check plus whether 443 is free and whether listeners could be inspected."""
    started = time.monotonic()
    result = conn.run("ss -H -tlnp sport = :443 2>/dev/null", timeout=10)
    listeners = result.stdout.strip()
    if result.returncode != 0:
        found = finding("port_443", "skipped", "Could not inspect port 443 listeners (ss is unavailable or failed)")
        return finish_check("port_443", "Port 443", [found], started), False, False
    if not listeners:
        found = finding("port_443", "passed", "Port 443 is available")
        return finish_check("port_443", "Port 443", [found], started), True, True
    match = re.search(r'users:\(\("([^"]*)"', listeners)
    owner = match.group(1) if match else "unknown"
    ownership = conn.run(_V4_STREAM_MARKER, timeout=10)
    if command_evidence_unavailable(ownership.returncode):
        found = finding("port_443", "skipped", "Could not verify whether the port 443 listener belongs to Meridian")
    elif owner in _MERIDIAN_LISTENERS and ownership.stdout.strip() == "managed":
        found = finding("port_443", "passed", f"Port 443 is in use by {owner} (Meridian)")
    else:
        found = finding(
            "port_443",
            "failed",
            f"Port 443 is in use by {owner}",
            "Stop or move that service; Meridian needs TCP 443.",
        )
    return finish_check("port_443", "Port 443", [found], started), False, True


def _port_443_external(
    conn: ServerConnection,
    probe_ip: str,
    port_free: bool,
    port_inspected: bool,
    local_vantage: bool,
) -> VerificationCheck:
    started = time.monotonic()
    reachable: bool | None
    if local_vantage:
        reachable = None
    elif port_free:
        reachable = _probe_unused_external_port(conn, probe_ip)
    else:
        reachable = tcp_connect(probe_ip, 443, timeout=5)
    if reachable is True:
        found = finding("port_443_external", "passed", "Port 443 is reachable from outside")
    elif reachable is None:
        message = (
            "External port 443 reachability needs a separate network vantage in local mode"
            if local_vantage
            else "Could not create a temporary listener to test external port 443 reachability"
        )
        found = finding("port_443_external", "skipped", message)
    elif not port_inspected:
        found = finding(
            "port_443_external",
            "skipped",
            "Port 443 is not externally reachable, and local listener evidence is unavailable",
        )
    elif port_free:
        found = finding(
            "port_443_external",
            "failed",
            "A temporary port 443 listener was not reachable from outside",
            _FIREWALL_HINT,
        )
    else:
        found = finding(
            "port_443_external",
            "failed",
            "Port 443 is listening on the server but not reachable from outside",
            _FIREWALL_HINT,
        )
    return finish_check("port_443_external", "Port 443 from outside", [found], started)


def _probe_unused_external_port(conn: ServerConnection, ip: str) -> bool | None:
    """Listen briefly without serving content so firewall reachability is testable."""
    family, bind = ("AF_INET6", "::") if ipaddress.ip_address(ip).version == 6 else ("AF_INET", "0.0.0.0")
    script = (
        f"import socket,time; s=socket.socket(socket.{family},socket.SOCK_STREAM); "
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        f"s.bind(('{bind}',443)); s.listen(1); time.sleep(10)"
    )
    try:
        started = conn.run(
            "command -v python3 >/dev/null 2>&1 || exit 127; "
            f"nohup python3 -c {shlex.quote(script)} </dev/null >/dev/null 2>&1 & echo $!",
            timeout=10,
        )
        pid = started.stdout.strip().splitlines()[-1] if started.stdout.strip() else ""
        if started.returncode != 0 or not pid.isdigit():
            return None
        q_pid = shlex.quote(pid)
        time.sleep(0.25)
        if conn.run(f"kill -0 {q_pid} 2>/dev/null", timeout=5).returncode != 0:
            return None
        try:
            return tcp_connect(ip, 443, timeout=5)
        finally:
            conn.run(f"kill {q_pid} 2>/dev/null || true", timeout=5)
    except (OSError, MeridianError):
        return None


def _domain_dns(conn: ServerConnection, domain: str, host: str, probe_ip: str) -> VerificationCheck:
    started = time.monotonic()
    result = conn.run(
        "command -v getent >/dev/null 2>&1 || exit 127; "
        f"getent ahosts {shlex.quote(domain)} 2>/dev/null | awk '{{print $1}}' | sort -u",
        timeout=10,
    )
    addresses: set[str] = set()
    for candidate in result.stdout.splitlines():
        try:
            addresses.add(str(ipaddress.ip_address(candidate.strip())))
        except ValueError:
            continue
    if command_evidence_unavailable(result.returncode):
        found = finding("domain_dns", "skipped", f"Could not test domain DNS for {domain} through the server resolver")
    elif probe_ip in addresses:
        found = finding("domain_dns", "passed", f"{domain} resolves to {host}")
    elif addresses:
        found = finding(
            "domain_dns",
            "failed",
            f"{domain} resolves to {', '.join(sorted(addresses))} (expected {host})",
            f"Point the A record for {domain} at {host} (DNS only while deploying).",
        )
    else:
        found = finding(
            "domain_dns", "failed", f"{domain} does not resolve", f"Create an A record for {domain} pointing at {host}."
        )
    return finish_check("domain_dns", "Domain DNS", [found], started)


def _server_os(facts: ServerFacts) -> VerificationCheck:
    started = time.monotonic()
    release = facts.os_release()
    name = release.pretty_name or release.id
    ubuntu = re.search(r"Ubuntu (\d+)\.(\d+)", name)
    found: VerificationFinding
    if not name:
        found = finding("server_os", "skipped", "Could not determine the server OS")
    elif ubuntu and not (int(ubuntu.group(1)) % 2 == 0 and int(ubuntu.group(2)) == 4):
        found = finding(
            "server_os",
            "warning",
            f"Server OS: {name} (non-LTS, supported for only 9 months)",
            "Reinstall with an Ubuntu LTS release for long-term support.",
        )
    elif "Ubuntu" in name or "Debian" in name:
        found = finding("server_os", "passed", f"Server OS: {name}")
    else:
        found = finding("server_os", "warning", f"Server OS: {name} (tested on Ubuntu/Debian only)")
    return finish_check("server_os", "Server OS", [found], started)


def _disk_space(facts: ServerFacts) -> VerificationCheck:
    started = time.monotonic()
    free_mb = facts.free_disk_mb("/")
    if free_mb is None:
        found = finding("disk_space", "skipped", "Could not determine available disk space")
    elif free_mb // 1024 >= 2:
        found = finding("disk_space", "passed", f"Disk space: {free_mb // 1024}G available")
    else:
        found = finding(
            "disk_space",
            "failed",
            f"Only {free_mb // 1024}G disk space available (need at least 2G)",
            "Free disk space or resize the server.",
        )
    return finish_check("disk_space", "Disk space", [found], started)


def _clock_sync(conn: ServerConnection, host: str) -> VerificationCheck:
    started = time.monotonic()
    result = conn.run("date +%s", timeout=10)
    server_epoch = result.stdout.strip()
    if not server_epoch.isdigit():
        found = finding("clock_sync", "skipped", "Could not determine server clock drift")
    else:
        drift = abs(int(server_epoch) - int(time.time()))
        if drift > 30:
            found = finding(
                "clock_sync",
                "failed",
                f"Clock drift: {drift}s between this device and the server (Reality needs <30s)",
                f"ssh root@{host} 'timedatectl set-ntp true', and enable automatic time on this device.",
            )
        else:
            found = finding("clock_sync", "passed", f"Clock sync OK ({drift}s drift)")
    return finish_check("clock_sync", "Clock sync", [found], started)
