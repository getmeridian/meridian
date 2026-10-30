#!/usr/bin/env python3
"""Check the delivered sing-box and Mihomo documents with each client's own engine."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient
from tests.systemlab.subscription_client import (
    ENGINE_NAMES,
    MIHOMO_CLIENT_TYPE,
    SINGBOX_CLIENT_TYPE,
    check_with_engine,
    fetch_canonical_document,
    parse_mihomo_subscription,
    parse_singbox_subscription,
)

_PARSERS = {
    SINGBOX_CLIENT_TYPE: parse_singbox_subscription,
    MIHOMO_CLIENT_TYPE: parse_mihomo_subscription,
}
_VERSION_COMMANDS = {
    SINGBOX_CLIENT_TYPE: ["sing-box", "version"],
    MIHOMO_CLIENT_TYPE: ["mihomo", "-v"],
}


def _engine_version(client_type: str) -> str:
    try:
        result = subprocess.run(_VERSION_COMMANDS[client_type], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"version unavailable ({exc})"
    lines = result.stdout.strip().splitlines()
    return lines[0] if lines else "version unavailable"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="acceptance")
    args = parser.parse_args()

    geodata = os.environ.get("MIHOMO_GEODATA_DIR")
    if not geodata or not Path(geodata).is_dir():
        print("FAIL: MIHOMO_GEODATA_DIR does not name the pinned geodata directory")
        return 1
    cluster = ClusterConfig.load()
    if not cluster.panel.url or not cluster.panel.api_token:
        print("FAIL: control service credentials are missing")
        return 1
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        documents = {
            client_type: fetch_canonical_document(client, args.username, client_type) for client_type in _PARSERS
        }

    failures = 0
    with tempfile.TemporaryDirectory(prefix="meridian-engines-") as workdir:
        for client_type, document in documents.items():
            engine = ENGINE_NAMES[client_type]
            print(f"  {engine}: {_engine_version(client_type)}")
            try:
                _PARSERS[client_type](document.content)
                check_with_engine(
                    client_type,
                    document.content,
                    Path(workdir),
                    geodata_dir=Path(geodata) if client_type == MIHOMO_CLIENT_TYPE else None,
                )
            except ValueError as exc:
                print(f"    FAIL {engine} document from {document.url}: {exc}")
                failures += 1
                continue
            print(f"    PASS {engine} accepts the delivered document")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
