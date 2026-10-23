"""Whether a node host reaches the encrypted resolvers its Xray resolves every name through.

Exit workloads resolve only over DNS-over-HTTPS (``xray_workload.EXIT_DOH_RESOLVERS``) and never
fall back to the host's plain resolver, so a host that reaches none of them cannot open sites by
name. One unreachable operator leaves fewer fallbacks; this check makes either visible.
"""

from __future__ import annotations

import shlex
import time
from typing import TYPE_CHECKING

from meridian.core.verification import VerificationCheck, VerificationFinding
from meridian.diagnostics.evidence import finding, finish_check
from meridian.xray_workload import EXIT_DOH_RESOLVERS, DohResolver

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

# RFC 8484's own example query (www.example.com, type A), base64url without padding.
_QUERY = "AAABAAABAAAAAAAAA3d3dwdleGFtcGxlA2NvbQAAAQAB"
_PER_ENDPOINT_SECONDS = 5


def check_exit_dns(conn: ServerConnection) -> VerificationCheck:
    """Ask every resolver endpoint once from the host, in parallel, and judge per operator."""
    started = time.monotonic()
    result = conn.run(_probe_command(), timeout=_PER_ENDPOINT_SECONDS + 20)
    return finish_check("exit_dns", "Encrypted DNS", [_judge(result.returncode, result.stdout)], started)


def _probe_command() -> str:
    probes = " ".join(
        f"( printf '%s %s\\n' {shlex.quote(resolver.url)} "
        f"\"$(curl -sS -o /dev/null -w '%{{http_code}}' --max-time {_PER_ENDPOINT_SECONDS} "
        f"-H 'accept: application/dns-message' {shlex.quote(f'{resolver.url}?dns={_QUERY}')} 2>/dev/null)\" ) &"
        for resolver in EXIT_DOH_RESOLVERS
    )
    return f"command -v curl >/dev/null 2>&1 || exit 127; {probes} wait"


def _judge(returncode: int, stdout: str) -> VerificationFinding:
    if returncode != 0:
        return finding("EXIT_DNS", "skipped", "Could not ask the encrypted resolvers from this server")
    codes: dict[str, str] = {}
    for line in stdout.splitlines():
        url, _, code = line.strip().partition(" ")
        codes[url] = code.strip()
    if any(resolver.url not in codes for resolver in EXIT_DOH_RESOLVERS):
        return finding("EXIT_DNS", "skipped", "Resolver reachability output was incomplete")

    operators = list(dict.fromkeys(resolver.operator for resolver in EXIT_DOH_RESOLVERS))
    answering = {resolver.operator for resolver in EXIT_DOH_RESOLVERS if codes[resolver.url] == "200"}
    silent = [operator for operator in operators if operator not in answering]
    if not silent:
        return finding("EXIT_DNS", "passed", f"{_join(operators)} answer DNS over HTTPS")
    if not answering:
        return finding(
            "EXIT_DNS",
            "failed",
            "No encrypted DNS resolver answers, so sites by name fail through this server; "
            "Meridian never falls back to plain DNS",
            f"Allow outbound TCP 443 to {_addresses(EXIT_DOH_RESOLVERS)} in the provider's firewall.",
        )
    unreachable = tuple(resolver for resolver in EXIT_DOH_RESOLVERS if resolver.operator in silent)
    return finding(
        "EXIT_DNS",
        "warning",
        f"{_join(silent)} {'does' if len(silent) == 1 else 'do'} not answer DNS over HTTPS; "
        f"{_join(sorted(answering, key=operators.index))} still resolve, with fewer fallbacks left",
        f"Allow outbound TCP 443 to {_addresses(unreachable)} in the provider's firewall.",
    )


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _addresses(resolvers: tuple[DohResolver, ...]) -> str:
    return _join([resolver.address for resolver in resolvers])
