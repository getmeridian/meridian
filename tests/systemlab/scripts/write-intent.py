#!/usr/bin/env python3
"""Write the complete V4 system-lab intent from registered server IDs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from meridian.config import SERVER_PROFILES_FILE
from meridian.servers import ServerRegistry
from tests.systemlab.topology import build_systemlab_intent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    registry = ServerRegistry(SERVER_PROFILES_FILE)

    def server_ref(environment_name: str) -> str:
        host = os.environ[environment_name]
        entry = registry.find(host)
        if entry is None:
            raise RuntimeError(f"{host} was not registered before intent generation")
        return entry.id

    intent = build_systemlab_intent(
        {
            "exit_a": server_ref("EXIT_A_IP"),
            "exit_b": server_ref("EXIT_B_IP"),
            "relay_a": server_ref("RELAY_A_IP"),
            "relay_b": server_ref("RELAY_B_IP"),
            "gateway": server_ref("GATEWAY_IP"),
        }
    )
    args.output.write_text(
        json.dumps(
            intent.model_dump(mode="json"),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
