"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "firewall": "ufw-comment-v1",
    "nginx": "per-resource-v2",
    "node-compose": "remnawave-node-v1",
    "realm": "hashed-systemd-v1",
    "xray-workload": "routing-vision-v2",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)
