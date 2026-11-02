"""Versioned digest for runtime renderers outside compiler payload models."""

from __future__ import annotations

from meridian.compiler.models import COMPILER_VERSION, canonical_hash

_RENDERER_MANIFEST = {
    "schema": "meridian.renderer-contract/v1",
    "compiler": COMPILER_VERSION,
    "agent-runtime": "capacity-telemetry-v12",
    "agent-spec": "access-profiles-v3",
    "control-subscriptions": "access-profiles-v4",
    "control-commands": "cut-off-disclosures-v8",
    "pwa": "access-profiles-v4",
    "control-service": "profile-use-v43",
    "control-api": "profile-declarations-v37",
    "control-ui": "profile-declarations-v27",
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
