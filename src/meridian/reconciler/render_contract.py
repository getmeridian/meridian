"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "agent-runtime": "signed-commands-v4",
    "agent-spec": "service-blob-v1",
    "control-subscriptions": "subscriber-device-slots-v2",
    "control-commands": "node-restart-console-v3",
    "pwa": "subscriber-device-slots-v1",
    "control-service": "device-exemption-v2",
    "control-api": "device-exemption-v2",
    "control-ui": "device-exemption-v2",
    "firewall": "ufw-comment-v2",
    "nginx": "panel-domain-certificate-v13",
    "realm": "neutral-systemd-musl-v3",
    "server-baseline": "lean-packages-v4",
    "xray-workload": "exit-doh-v8",
}

RENDERER_CONTRACT = "meridian.renderer-contract/v1@sha256:" + canonical_hash(_RENDERER_MANIFEST)


def renderer_stamp(component: str) -> str:
    """One renderer's own stamp, for attestations that must not follow every other renderer's bump."""
    return f"{component}={_RENDERER_MANIFEST[component]}"
