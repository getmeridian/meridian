"""Finite firewall resources shared by topology compiler phases."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.models import FirewallRulePayload


def add_firewall(
    builder: PlanBuilder,
    server_ref: str,
    transport: Literal["tcp", "udp"],
    port: int,
    *,
    source_server_refs: list[str] | None = None,
) -> str:
    source_refs = sorted(set(source_server_refs or []))
    source_identity = ",".join(source_refs) or "public"
    logical_id = f"firewall:{stable_token(server_ref)}:{transport}:{port}:{stable_token(source_identity)}"
    if logical_id in builder.resources:
        return logical_id
    return builder.add(
        logical_id,
        FirewallRulePayload(
            server_ref=server_ref,
            transport=transport,
            port=port,
            source_server_refs=source_refs,
        ),
    )


def stable_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "resource"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{normalized[:32]}-{digest}"
