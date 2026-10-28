#!/usr/bin/env python3
"""Record what the public subscription URL serves to each client User-Agent.

Writes raw, un-normalized captures (they hold live secrets) below ``--out/<topology>/``:
one JSON file per request plus ``context.json``, the values the normalizer must replace.
``python -m tests.systemlab.golden_corpus normalize`` turns them into fixtures.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

from meridian.cluster import ClusterConfig
from meridian.remnawave import MeridianPanel
from meridian.xray_workload import salamander_password
from tests.systemlab.golden_agents import (
    AGENTS,
    AGENTS_BY_ID,
    SELECTOR_AGENT_ID,
    SELECTORS,
    STATUS_AGENT_IDS,
    UNKNOWN_AGENT_IDS,
    ClientAgent,
)
from tests.systemlab.golden_corpus import HOP_BY_HOP_HEADERS, LAB_HOST_ROLES

# A well-formed short UUID no user holds, for the not-found answer.
UNKNOWN_SHORT_UUID = "golden0unknown0subscription"
HISTORY_WAIT_SECONDS = 15


def _known_values(cluster: ClusterConfig, users: list[Any]) -> list[list[str]]:
    known: list[tuple[str, str]] = []
    for user in users:
        raw = user._raw
        known += [("short-uuid", user.short_uuid), ("vless-uuid", user.vless_uuid)]
        known += [("user-uuid", str(raw.get("uuid") or "")), ("password", str(raw.get("trojanPassword") or ""))]
        known += [("password", str(raw.get("ssPassword") or ""))]
    known += [("secret-path", cluster.panel.secret_path), ("sub-path", cluster.panel.sub_path)]
    known += [("secret", cluster.panel.api_token), ("secret", cluster.panel.admin_pass)]
    if cluster.subscription_page:
        known.append(("page-path", cluster.subscription_page.path))
    for workload in cluster.workloads:
        for keys in workload.reality_keys.values():
            known += [("reality-pbk", keys.public_key), ("short-id", keys.short_id)]
            known += [("salamander", salamander_password(keys)), ("secret", keys.private_key)]
    known += [("path", allocation.path) for allocation in cluster.allocations.values()]
    if cluster.topology_intent is not None:
        known += [("path", path.path) for exit_ in cluster.topology_intent.exits for path in exit_.paths]
    for variable, role in LAB_HOST_ROLES.items():
        known.append((f"host:{role}", os.environ[variable]))
    return [[kind, value] for kind, value in dict.fromkeys(known) if value]


def _history(client: httpx.Client, user_id: str) -> list[dict[str, Any]]:
    response = client.get(f"api/users/{user_id}/subscription-request-history")
    response.raise_for_status()
    return list(response.json()["response"]["records"])


def _matched(client: httpx.Client, user_id: str, seen: set[int]) -> dict[str, Any] | None:
    """The rule Remnawave recorded for the request just made (written through a queue)."""
    deadline = time.monotonic() + HISTORY_WAIT_SECONDS
    while time.monotonic() < deadline:
        fresh = [record for record in _history(client, user_id) if record["id"] not in seen]
        if fresh:
            record = max(fresh, key=lambda item: item["id"])
            seen.update(item["id"] for item in fresh)
            return {"rule": record.get("srrRuleName"), "response_type": record.get("srrResponseType")}
        time.sleep(0.5)
    return None


def _fetch(url: str, agent: ClientAgent) -> httpx.Response:
    headers = {"Accept": agent.accept, "Accept-Encoding": "gzip"}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        request = client.build_request("GET", url, headers=headers)
        if agent.user_agent is None:
            request.headers.pop("User-Agent", None)
        else:
            request.headers["User-Agent"] = agent.user_agent
        return client.send(request)


def _record(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _capture(
    out: Path,
    *,
    topology: str,
    request_id: str,
    user: str,
    url: str,
    agent: ClientAgent,
    selector: str = "",
    history: tuple[httpx.Client, str, set[int]] | None = None,
) -> None:
    response = _fetch(url, agent)
    matched = _matched(*history) if history is not None and response.status_code == 200 else None
    headers = [
        [name.decode("latin-1").lower(), value.decode("latin-1")]
        for name, value in response.headers.raw
        if name.decode("latin-1").lower() not in HOP_BY_HOP_HEADERS
    ]
    _record(
        out / f"{request_id}.json",
        {
            "topology": topology,
            "request": {
                "id": request_id,
                "agent": agent.id,
                "user": user,
                "selector": selector,
                "user_agent": agent.user_agent,
                "accept": agent.accept,
            },
            "matched": matched,
            "response": {
                "status": response.status_code,
                "headers": headers,
                "body_b64": base64.b64encode(response.content).decode("ascii"),
            },
        },
    )
    print(f"    {request_id}: {response.status_code} {response.headers.get('content-type', '')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topology", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--state", choices=("active", "disabled"), default="active")
    parser.add_argument("--unknown", action="store_true", help="also capture a short UUID nobody holds")
    parser.add_argument("--out", type=Path, default=Path(os.environ.get("SYSTEMLAB_GOLDEN_RAW", "/tmp/golden-raw")))
    args = parser.parse_args()

    out = args.out / args.topology
    out.mkdir(parents=True, exist_ok=True)
    cluster = ClusterConfig.load()
    with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
        user = panel.get_user(args.user)
        settings = panel.get_subscription_settings()
    assert user is not None and user.short_uuid, f"{args.user} has no subscription"
    expected_status = "ACTIVE" if args.state == "active" else "DISABLED"
    assert user.status == expected_status, (user.status, expected_status)

    context_path = out / "context.json"
    context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}
    known = [*context.get("known", []), *_known_values(cluster, [user])]
    context.update(
        {
            "topology": args.topology,
            "meridian_version": version("meridian-vpn"),
            "known": [list(pair) for pair in dict.fromkeys(tuple(pair) for pair in known)],
            "response_rules": settings.response_rules,
            "serve_json_at_base_subscription": settings.serve_json_at_base_subscription,
        }
    )
    _record(context_path, context)

    url = cluster.panel.subscription_url(user.short_uuid)
    label = "" if args.state == "active" else f"{args.state}--"
    agents = AGENTS if args.state == "active" else tuple(AGENTS_BY_ID[agent_id] for agent_id in STATUS_AGENT_IDS)
    authorization = {"Authorization": f"Bearer {cluster.panel.api_token}"}
    with httpx.Client(base_url=cluster.panel.url.rstrip("/") + "/", headers=authorization, timeout=30) as api:
        history = (api, user.id, {record["id"] for record in _history(api, user.id)})
        for agent in agents:
            _capture(
                out,
                topology=args.topology,
                request_id=f"{label}{agent.id}",
                user=args.state,
                url=url,
                agent=agent,
                history=history,
            )
        if args.state == "active":
            for selector in SELECTORS:
                _capture(
                    out,
                    topology=args.topology,
                    request_id=f"selector-{selector}",
                    user=args.state,
                    url=f"{url}/{selector}",
                    agent=AGENTS_BY_ID[SELECTOR_AGENT_ID],
                    selector=selector,
                    history=history,
                )
    if args.unknown:
        unknown_url = cluster.panel.subscription_url(UNKNOWN_SHORT_UUID)
        for agent_id in UNKNOWN_AGENT_IDS:
            _capture(
                out,
                topology=args.topology,
                request_id=f"unknown--{agent_id}",
                user="unknown",
                url=unknown_url,
                agent=AGENTS_BY_ID[agent_id],
            )


if __name__ == "__main__":
    main()
