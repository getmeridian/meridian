"""Deterministic listener allocation for the pure topology compiler."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from meridian.compiler.errors import TopologyCompileError
from meridian.core.topology import ExitIntent, ProtocolKind, ProtocolPathIntent


@dataclass
class PortAllocator:
    used: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))

    def path_port(self, exit_: ExitIntent, path: ProtocolPathIntent) -> int:
        transport = "udp" if path.protocol == "hysteria2" else "tcp"
        key = (exit_.server_ref, transport)
        if path.listen_port:
            return self._reserve(key, path.listen_port, f"{exit_.id}/{path.id}")
        if path.protocol == "hysteria2":
            return self._reserve(key, path.public_port, f"{exit_.id}/{path.id}")
        ranges: dict[ProtocolKind, tuple[int, int]] = {
            "reality": (10000, 1999),
            "wss": (20000, 9999),
            "xhttp": (30000, 9999),
            "hysteria2": (443, 1),
        }
        base, size = ranges[path.protocol]
        return self._allocate(key, f"path:{exit_.id}:{path.id}", base, size)

    def auxiliary_tcp(self, server_ref: str, identity: str, *, base: int, size: int) -> int:
        return self._allocate((server_ref, "tcp"), identity, base, size)

    def _allocate(self, key: tuple[str, str], identity: str, base: int, size: int) -> int:
        offset = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) % size
        for increment in range(size):
            candidate = base + ((offset + increment) % size)
            if candidate not in self.used[key]:
                self.used[key].add(candidate)
                return candidate
        raise TopologyCompileError(f"No free deterministic ports remain for {key[0]} {key[1]}.")

    def _reserve(self, key: tuple[str, str], port: int, owner: str) -> int:
        if port in self.used[key]:
            raise TopologyCompileError(f"{key[0]} has conflicting {key[1]} listener port {port} at {owner}.")
        self.used[key].add(port)
        return port
