"""Pure helpers for the `domain` topology's verifier (verify/domain.sh).

The shell script drives the real CLI against a real VM; everything that can be decided
without a server lives here so CI can pin it: the panel name, the setup intent, the edit
that moves exit paths onto the panel's domain, reading `meridian test`'s counts, and telling
a Let's Encrypt rate limit (inconclusive) from a product failure.

Usage (from the repository root, via `uv run python -m tests.realvm.domain_support ...`):
    panel-domain IP                      print the panel's name (REALVM_PANEL_DOMAIN or sslip.io)
    intent SERVER_REF IP DOMAIN [--shared]  print the SetupIntent JSON, exit paths on the IP (or on DOMAIN)
    switch FILE IP DOMAIN                rewrite an exported fleet file so exit paths use DOMAIN
    counts                               read a `--json` envelope on stdin, print "passed failed warnings skipped"
    rate-limited                         read output on stdin; exit 0 if Let's Encrypt refused to issue
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from meridian.core.topology import SetupIntent
from meridian.setup.single_server import single_server_intent

# sslip.io answers `203-0-113-10.sslip.io` with 203.0.113.10, so any VM gets a real DNS name.
SSLIP_SUFFIX = "sslip.io"
PANEL_DOMAIN_ENV = "REALVM_PANEL_DOMAIN"
# The verifier's exit code for "could not conclude" (BSD EX_TEMPFAIL); 0 passed, 1 findings, 2 refused.
INCONCLUSIVE_EXIT = 75
_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# Meridian's own wording (provision/tls.py) plus acme.sh's, since either may reach the log.
_RATE_LIMIT_MARKERS = (
    "urn:ietf:params:acme:error:ratelimited",
    "retry after",
    "refused a new certificate",
    "has issued its limit",
)


def sslip_domain(ip: str) -> str:
    """The sslip.io name that resolves to an IPv4 address."""
    address = ipaddress.IPv4Address(ip)
    return f"{str(address).replace('.', '-')}.{SSLIP_SUFFIX}"


def panel_domain(ip: str, override: str = "") -> str:
    """The panel's domain: the operator's own name pointed at the VM, else the sslip.io name."""
    chosen = override.strip().lower().rstrip(".")
    if not chosen:
        return sslip_domain(ip)
    if not _HOSTNAME.match(chosen):
        raise ValueError(f"{PANEL_DOMAIN_ENV} must be a hostname, got {override!r}")
    return chosen


def build_intent(server_ref: str, ip: str, domain: str, *, shared: bool = False, client_name: str = "alice") -> str:
    """The setup intent JSON: the panel on `domain`, exit paths on the IP unless `shared`."""
    intent = single_server_intent(
        None,
        server_ref=server_ref,
        address=ip,
        domain=domain if shared else "",
        client_name=client_name,
    )
    data = intent.model_dump(mode="json")
    data["control"]["public_hostname"] = domain
    return SetupIntent.model_validate(data).model_dump_json(indent=2)


def switch_exit_paths(document: str, ip: str, domain: str) -> str:
    """Rewrite an exported fleet file so every exit path naming `ip` names `domain` instead.

    Only `tls_sni` and `host` on exit paths change; a file with nothing to switch is an error,
    since the next assertions would otherwise pass on an unchanged fleet.
    """
    data = yaml.safe_load(document)
    switched = 0
    for exit_ in data.get("exits", []):
        for path in exit_.get("paths", []):
            for key in ("tls_sni", "host"):
                if path.get(key) == ip:
                    path[key] = domain
                    switched += 1
    if not switched:
        raise ValueError(f"no exit path names {ip}; nothing to switch to {domain}")
    return yaml.safe_dump(data, sort_keys=False)


def verification_counts(envelope: str) -> tuple[int, int, int, int]:
    """(passed, failed, warnings, skipped) from a `meridian --json test|check` envelope."""
    counts = _json_object(envelope)["summary"]["counts"]
    return counts["passed"], counts["failed"], counts["warnings"], counts["skipped"]


def is_rate_limited(output: str) -> bool:
    """Whether a deploy log shows Let's Encrypt refusing a certificate for rate-limit reasons."""
    lowered = output.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _json_object(text: str) -> dict[str, Any]:
    """The first JSON object in `text`; commands may print notices before the envelope."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in output")
    value, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("JSON output is not an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.realvm.domain_support", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("panel-domain").add_argument("ip")
    intent = sub.add_parser("intent")
    intent.add_argument("server_ref")
    intent.add_argument("ip")
    intent.add_argument("domain")
    intent.add_argument("--shared", action="store_true", help="put the exit paths on DOMAIN too")
    switch = sub.add_parser("switch")
    switch.add_argument("file", type=Path)
    switch.add_argument("ip")
    switch.add_argument("domain")
    sub.add_parser("counts")
    sub.add_parser("rate-limited")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "panel-domain":
            print(panel_domain(args.ip, os.environ.get(PANEL_DOMAIN_ENV, "")))
        elif args.cmd == "intent":
            print(build_intent(args.server_ref, args.ip, args.domain, shared=args.shared))
        elif args.cmd == "switch":
            args.file.write_text(switch_exit_paths(args.file.read_text(), args.ip, args.domain))
        elif args.cmd == "counts":
            print(*verification_counts(sys.stdin.read()))
        else:
            return 0 if is_rate_limited(sys.stdin.read()) else 1
    except (ValueError, KeyError, TypeError) as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
