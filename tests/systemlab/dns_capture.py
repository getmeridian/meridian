#!/usr/bin/env python3
"""Count packets that carry a plain DNS question for given names, on every interface.

Runs on a lab server as ``python3 - --names a.example,b.example --ready R --stop S < dns_capture.py``
(stdlib only: the servers have no Meridian checkout). It matches the wire-format question name
(length-prefixed labels) in any packet on any port, loopback included, so Docker's embedded
resolver (127.0.0.11, NATed to a random port) counts too. TLS SNI and HTTP Host carry dotted names
and DoH is encrypted, so neither matches.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

_ETH_P_ALL = 0x0003
_MAX_PACKET = 65535


def wire_name(hostname: str) -> bytes:
    """Return a hostname as it appears in a DNS question, lowercased."""
    labels = hostname.strip(".").lower().split(".")
    if not all(0 < len(label) < 64 for label in labels):
        raise ValueError(f"not a DNS name: {hostname!r}")
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\x00"


def count_questions(packets: list[bytes], names: list[str]) -> dict[str, int]:
    """Count the packets that carry each name's DNS question, ignoring 0x20 case randomization."""
    wires = {name: wire_name(name) for name in names}
    counts = dict.fromkeys(names, 0)
    for packet in packets:
        # Label lengths stay below 0x41, so lowering the packet never alters them.
        lowered = packet.lower()
        for name, wire in wires.items():
            if wire in lowered:
                counts[name] += 1
    return counts


def capture(names: list[str], *, ready: Path, stop: Path, max_seconds: float) -> dict[str, int]:
    """Sniff until the stop file appears (or the time limit passes) and count matches."""
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(_ETH_P_ALL))
    sock.settimeout(0.5)
    counts = dict.fromkeys(names, 0)
    seen = 0
    ready.touch()
    deadline = time.monotonic() + max_seconds
    try:
        while not stop.exists() and time.monotonic() < deadline:
            try:
                packet = sock.recv(_MAX_PACKET)
            except TimeoutError:
                continue
            seen += 1
            for name, count in count_questions([packet], names).items():
                counts[name] += count
    finally:
        sock.close()
    return {**counts, "__packets__": seen}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", required=True, help="Comma-separated hostnames")
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args()
    names = [name for name in args.names.split(",") if name]
    print(json.dumps(capture(names, ready=args.ready, stop=args.stop, max_seconds=args.max_seconds)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
