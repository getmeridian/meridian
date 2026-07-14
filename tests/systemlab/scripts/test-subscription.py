#!/usr/bin/env python3
"""Execute the canonical Xray document returned by Remnawave."""

from __future__ import annotations

import argparse
import sys

from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel
from meridian.xray_client import ensure_xray_binary, test_connection
from tests.systemlab.subscription_client import (
    endpoint_addresses,
    fetch_canonical_xray,
    missing_addresses,
    parse_xray_subscription,
    replace_user_ids,
    route_only,
    select_outbound,
    use_free_local_ports,
)

_BOGUS_UUID = "00000000-0000-0000-0000-000000000000"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--username",
        default="acceptance",
    )
    parser.add_argument(
        "--address",
        action="append",
        default=[],
        help="Pin and execute one delivered endpoint; repeatable",
    )
    parser.add_argument(
        "--require-address",
        action="append",
        default=[],
        help="Require endpoint presence without executing it",
    )
    parser.add_argument(
        "--forbid-address",
        action="append",
        default=[],
        help="Require endpoint absence from canonical delivery",
    )
    parser.add_argument(
        "--automatic",
        action="store_true",
        help="Execute the delivered automatic failover policy",
    )
    parser.add_argument(
        "--bogus-credentials",
        action="store_true",
        help="Replace delivered user IDs before execution",
    )
    parser.add_argument(
        "--expect-failure",
        action="store_true",
        help="Pass only when every requested execution is blocked",
    )
    args = parser.parse_args()

    cluster = ClusterConfig.load()
    if not cluster.panel.url or not cluster.panel.api_token:
        print("FAIL: panel credentials are missing")
        return 1
    with MeridianPanel(
        cluster.panel.url,
        cluster.panel.api_token,
    ) as panel:
        document = fetch_canonical_xray(
            panel,
            args.username,
        )
    config = parse_xray_subscription(document.content)
    print(f"  canonical URL: {document.url}")
    print("  delivered endpoints: " + ", ".join(sorted(endpoint_addresses(config))))

    missing = missing_addresses(
        config,
        [*args.address, *args.require_address],
    )
    if missing:
        print("FAIL: canonical subscription omitted: " + ", ".join(sorted(missing)))
        return 1
    forbidden = endpoint_addresses(config).intersection(args.forbid_address)
    if forbidden:
        print("FAIL: canonical subscription exposed: " + ", ".join(sorted(forbidden)))
        return 1

    xray_bin = ensure_xray_binary()
    if not xray_bin:
        print("FAIL: could not obtain Xray binary")
        return 1

    requests: list[tuple[str, dict]] = []
    for address in args.address:
        tag = select_outbound(config, address)
        requests.append(
            (
                f"canonical endpoint {address}",
                route_only(config, tag),
            )
        )
    if args.automatic:
        requests.append(("canonical automatic failover", config))
    if not requests:
        print("FAIL: choose --address or --automatic")
        return 1

    failures = 0
    for label, selected in requests:
        if args.bogus_credentials:
            selected = replace_user_ids(
                selected,
                _BOGUS_UUID,
            )
            label += " with bogus credentials"
        runnable, socks_port = use_free_local_ports(selected)
        connected, detail = test_connection(
            xray_bin,
            runnable,
            "",
            socks_port,
            label,
            expect_ip_match=False,
        )
        accepted = not connected if args.expect_failure else connected
        status = "PASS" if accepted else "FAIL"
        expectation = "blocked" if args.expect_failure else "connected"
        print(f"    {status} {label}: expected {expectation}; {detail}")
        if not accepted:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
