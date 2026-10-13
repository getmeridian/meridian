"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "pwa": "connection-page-v2",
    "firewall": "ufw-comment-v2",
    "nginx": "per-resource-v4",
    "node-compose": "remnawave-node-v2",
    "panel-compose": "remnawave-panel-v2",
    "realm": "hashed-systemd-v1",
    "xray-workload": "reality-min-client-v4",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)
