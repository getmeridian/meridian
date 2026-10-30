"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "agent-runtime": "signed-commands-v4",
    "agent-spec": "service-blob-v1",
    "control-subscriptions": "service-pages-v14",
    "control-commands": "node-restart-console-v3",
    "pwa": "service-pages-v25",
    "control-service": "person-connections-v1",
    "control-api": "person-expiry-v1",
    "control-ui": "person-view-v4",
    "firewall": "ufw-comment-v2",
    "nginx": "service-pages-v12",
    "realm": "neutral-systemd-musl-v3",
    "server-baseline": "lean-packages-v4",
    "xray-workload": "exit-doh-v8",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)


def renderer_stamp(component: str) -> str:
    """One renderer's own stamp, for attestations that must not follow every other renderer's bump."""
    return f"{component}={_RENDERER_MANIFEST[component]}"
