"""Self-steal camouflage: Reality imitates the exit's own hostname and nginx.

A borrowed camouflage site lives elsewhere, so its name and the exit's IP never match.
With self-steal the Reality SNI is the operator's hostname, pointed at the exit: probes
and unknown SNIs reach a loopback nginx TLS server holding that hostname's Let's Encrypt
certificate. When XHTTP or WSS share the hostname, their TLS server is that fallback,
and the 443 stream sends the name to Reality, which passes non-Reality TLS on to it.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.compiler.allocations import PortAllocator
from meridian.compiler.builder import PlanBuilder
from meridian.compiler.certificates import ensure_certificate
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.firewalls import stable_token
from meridian.compiler.models import NginxArtifactPayload, ResourcePostcondition
from meridian.core.topology import SetupIntent


@dataclass(frozen=True)
class SelfStealDecoy:
    """The loopback nginx TLS server one exit's Reality path falls back to."""

    exit_ref: str
    server_ref: str
    hostname: str
    port: int
    reality_public_port: int

    @property
    def stream_key(self) -> tuple[str, int]:
        return (self.server_ref, self.reality_public_port)

    @property
    def http_id(self) -> str:
        return decoy_http_id(self.exit_ref, self.hostname)


def decoy_http_id(exit_ref: str, hostname: str) -> str:
    """The nginx TLS server artifact for one exit hostname (shared with XHTTP/WSS on it)."""
    return f"nginx:{exit_ref}:{stable_token(hostname)}:http"


def plan_self_steal(intent: SetupIntent, ports: PortAllocator) -> dict[str, SelfStealDecoy]:
    """Allocate each self-steal exit's fallback server, keyed by exit ID."""
    decoys: dict[str, SelfStealDecoy] = {}
    for exit_ in sorted(intent.exits, key=lambda item: item.id):
        path = next(path for path in exit_.paths if path.protocol == "reality")
        if not path.self_steal:
            continue
        if exit_.server_ref == intent.control.server_ref and path.reality_sni == intent.control.public_hostname:
            raise TopologyCompileError(
                f"Exit {exit_.id} uses the panel domain {path.reality_sni} for self-steal; "
                "give self-steal its own hostname."
            )
        decoys[exit_.id] = SelfStealDecoy(
            exit_ref=exit_.id,
            server_ref=exit_.server_ref,
            hostname=path.reality_sni,
            port=ports.auxiliary_tcp(
                exit_.server_ref, f"tls:{exit_.id}:{path.reality_sni}:{path.public_port}", base=18000, size=1000
            ),
            reality_public_port=path.public_port,
        )
    return decoys


def add_decoy_server(builder: PlanBuilder, decoy: SelfStealDecoy, *, runtime_id: str) -> str:
    """The fallback TLS server for a hostname no XHTTP/WSS path already serves: only nginx's 404."""
    certificate_id = ensure_certificate(
        builder,
        exit_id=decoy.exit_ref,
        server_ref=decoy.server_ref,
        hostname=decoy.hostname,
        runtime_id=runtime_id,
    )
    return builder.add(
        decoy.http_id,
        NginxArtifactPayload(
            server_ref=decoy.server_ref,
            listener_port=decoy.port,
            layer="http",
            tls_hostname=decoy.hostname,
            routes=[],
        ),
        dependencies=[certificate_id],
        postconditions=[
            ResourcePostcondition(kind="exists", target_ref=decoy.http_id),
            ResourcePostcondition(kind="listening", target_ref=decoy.http_id, detail=str(decoy.port)),
        ],
    )
