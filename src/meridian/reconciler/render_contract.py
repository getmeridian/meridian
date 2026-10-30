"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "agent-runtime": "signed-commands-v4",
    "agent-spec": "service-blob-v1",
    "control-subscriptions": "service-pages-v12",
    "control-commands": "node-restart-console-v3",
    "pwa": "service-pages-v24",
    "control-service": "service-pages-v21",
    "control-api": "service-pages-v6",
    "firewall": "ufw-comment-v2",
    "nginx": "service-pages-v12",
    "realm": "neutral-systemd-musl-v3",
    "server-baseline": "lean-packages-v4",
    "xray-workload": "exit-doh-v8",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)
