#!/usr/bin/env python3
"""Replay the golden corpus against the live control service and diff every answer.

The corpus (`tests/systemlab/golden/`) records what Remnawave served each client User-Agent
on this lab's fleets. Each recorded request for ``--topology`` and ``--state`` is sent to the
fleet's public subscription URL, normalized like the corpus, and compared with the answer
`tests/test_control_subscription_corpus.py` expects: the recorded one, or the named deviation.
Headers nginx adds in front of the service are not the service's answer and are ignored.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient
from meridian.control.formats.selector import select_format
from meridian.xray_workload import salamander_password
from tests.systemlab.golden_corpus import HOP_BY_HOP_HEADERS, LAB_HOST_ROLES, Capture, decode_body, normalize_response
from tests.test_control_subscription_corpus import CORPUS, DROPPED_HEADERS, _differences, _expected

# A well-formed subscription token nobody holds, for the not-found answer.
UNKNOWN_TOKEN = "golden0unknown0subscription"


def _known_values(cluster: ClusterConfig, sub_token: str, uuid: str) -> list[tuple[str, str]]:
    """The live secrets and addresses the normalizer replaces, in the corpus's kinds."""
    known = [("short-uuid", sub_token), ("vless-uuid", uuid)]
    known += [("secret-path", cluster.panel.secret_path), ("sub-path", cluster.panel.sub_path)]
    known += [("secret", cluster.panel.api_token)]
    for workload in cluster.workloads:
        for keys in workload.reality_keys.values():
            known += [("reality-pbk", keys.public_key), ("short-id", keys.short_id)]
            known += [("salamander", salamander_password(keys)), ("secret", keys.private_key)]
    known += [("path", allocation.path) for allocation in cluster.allocations.values()]
    if cluster.topology_intent is not None:
        known += [("path", path.path) for exit_ in cluster.topology_intent.exits for path in exit_.paths]
    for variable, role in LAB_HOST_ROLES.items():
        known.append((f"host:{role}", os.environ[variable]))
    return [pair for pair in dict.fromkeys(known) if pair[1]]


def _fetch(url: str, capture: Capture) -> httpx.Response:
    request = capture.request
    headers = {"Accept": str(request["accept"]), "Accept-Encoding": "gzip"}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        built = client.build_request("GET", url, headers=headers)
        if request["user_agent"] is None:
            built.headers.pop("User-Agent", None)
        else:
            built.headers["User-Agent"] = str(request["user_agent"])
        return client.send(built)


def _check_page(url: str, capture: Capture) -> bool:
    """A browser navigation gets the connection page, which the corpus predates; its JSON moved to /info."""
    response = _fetch(url, capture)
    content_type = response.headers.get("content-type", "")
    problems = []
    if response.status_code != 200:
        problems.append(f"status {response.status_code}")
    if not content_type.startswith("text/html"):
        problems.append(f"content-type {content_type}")
    if "content-security-policy" not in response.headers:
        problems.append("no content-security-policy")
    if response.headers.get("cache-control") != "no-store":
        problems.append(f"cache-control {response.headers.get('cache-control')}")
    print(f"    {'FAIL' if problems else 'PASS'} {capture.request['id']} (page): {response.status_code} html")
    for problem in problems:
        print("      " + problem)
    return bool(problems)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--state", choices=("active", "disabled"), default="active")
    parser.add_argument("--unknown", action="store_true", help="also replay the unknown-subscription requests")
    args = parser.parse_args()

    cluster = ClusterConfig.load()
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        user = client.get_user(args.user)
    assert user is not None and user.sub_token, f"{args.user} has no subscription"
    assert user.status == args.state, (user.status, args.state)
    known = _known_values(cluster, user.sub_token, user.uuid)

    states = {args.state, "unknown"} if args.unknown else {args.state}
    captures = [
        capture
        for (topology, _), capture in sorted(CORPUS.items())
        if topology == args.topology and capture.request["user"] in states
    ]
    assert captures, f"the corpus records no {sorted(states)} requests for {args.topology}"

    failures = 0
    for capture in captures:
        request = capture.request
        token = UNKNOWN_TOKEN if request["user"] == "unknown" else user.sub_token
        page = (
            request["user"] != "unknown"
            and not request["selector"]
            and select_format(request["user_agent"], request["accept"]) == "browser"
        )
        page_failed = page and _check_page(cluster.panel.subscription_url(token), capture)
        suffix = "/info" if page else (f"/{request['selector']}" if request["selector"] else "")
        url = cluster.panel.subscription_url(token) + suffix
        response = _fetch(url, capture)
        headers = [
            (name.decode("latin-1").lower(), value.decode("latin-1"))
            for name, value in response.headers.raw
            if name.decode("latin-1").lower() not in HOP_BY_HOP_HEADERS | DROPPED_HEADERS
        ]
        content_type = response.headers.get("content-type", "")
        encoding, text = decode_body(content_type, response.content)
        served = normalize_response(response.status_code, headers, text, encoding=encoding, known=known)
        problems = _differences(_expected(capture), served)
        status = "FAIL" if problems else "PASS"
        print(f"    {status} {request['id']}: {response.status_code} {served.format}")
        for problem in problems:
            print("      " + problem.replace("\n", "\n      "))
        failures += bool(problems) or page_failed
    print(f"    {len(captures) - failures} of {len(captures)} answers match the corpus")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
