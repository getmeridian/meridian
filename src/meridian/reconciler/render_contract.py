"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "pwa": "connection-page-v3",
    "firewall": "ufw-comment-v2",
    "nginx": "ip-locations-v7",
    "node-compose": "remnawave-node-v3",
    "panel-compose": "remnawave-panel-v2",
    "realm": "neutral-systemd-v2",
    "server-baseline": "neutral-paths-v2",
    "xray-workload": "salamander-v6",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)
