"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "pwa": "support-page-v9",
    "firewall": "ufw-comment-v2",
    "nginx": "upload-size-v9",
    "node-compose": "neutral-names-v4",
    "panel-compose": "neutral-names-v3",
    "realm": "neutral-systemd-musl-v3",
    "server-baseline": "fail2ban-journal-v3",
    "xray-workload": "sniffing-v7",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)
