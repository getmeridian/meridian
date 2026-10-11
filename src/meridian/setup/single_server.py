"""Build the topology intent behind `meridian deploy IP`: one server, one exit.

The control plane stays on the server's IP certificate; a domain, when given,
carries the exit's TLS paths (XHTTP, WSS, Hysteria2). One server cannot route
the same SNI to both the control plane and an exit.
"""

from __future__ import annotations

import secrets
from typing import Literal

from meridian.core.defaults import DEFAULT_SNI
from meridian.core.errors import LocalStateError
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)

DEFAULT_EXIT_ID = "main"
DEFAULT_CLIENT = "default"


def single_server_intent(
    existing: SetupIntent | None,
    *,
    server_ref: str,
    sni: str = "",
    domain: str = "",
    client_name: str = "",
    warp: bool = False,
    geo_block: bool = True,
    hysteria2: bool = False,
) -> SetupIntent:
    """Return the intent for deploying (or redeploying) one server.

    A fresh fleet gets a control plane plus one exit on ``server_ref``. An
    existing fleet may only be redeployed on a server that already runs an
    exit; its other exits, relays, gateways, and users are preserved.
    """
    if hysteria2 and not domain:
        raise LocalStateError(
            "Hysteria2 needs a domain for its TLS certificate.",
            hint="Pass --domain, or drop --hysteria2.",
            category="user",
        )
    if existing is None:
        exit_ = ExitIntent(
            id=DEFAULT_EXIT_ID,
            server_ref=server_ref,
            paths=_paths(DEFAULT_EXIT_ID, None, sni=sni, domain=domain, hysteria2=hysteria2),
            warp=warp,
            geo_block=geo_block,
        )
        return SetupIntent(
            control=ControlPlaneIntent(server_ref=server_ref),
            exits=[exit_],
            default_egress_ref=exit_.id,
            access=AccessIntent(users=[client_name or DEFAULT_CLIENT]),
        )

    current = next((item for item in existing.exits if item.server_ref == server_ref), None)
    if current is None:
        raise LocalStateError(
            "This server is not an exit in your saved fleet.",
            hint="Add it with `meridian node add`, or change the fleet with `meridian setup`.",
            category="user",
        )
    updated = current.model_copy(
        update={
            "paths": _paths(current.id, current, sni=sni, domain=domain, hysteria2=hysteria2),
            "warp": warp,
            "geo_block": geo_block,
        }
    )
    users = list(existing.access.users)
    if client_name and client_name not in users:
        users.append(client_name)
    return existing.model_copy(
        update={
            "exits": [updated if item.id == current.id else item for item in existing.exits],
            "access": existing.access.model_copy(update={"users": users}),
        }
    )


def _paths(
    exit_id: str,
    current: ExitIntent | None,
    *,
    sni: str,
    domain: str,
    hysteria2: bool,
) -> list[ProtocolPathIntent]:
    previous: dict[str, ProtocolPathIntent] = {path.protocol: path for path in current.paths} if current else {}
    reality_sni = sni or (previous["reality"].reality_sni if "reality" in previous else DEFAULT_SNI)
    paths = [ProtocolPathIntent(id=f"{exit_id}-reality", protocol="reality", reality_sni=reality_sni)]
    if not domain:
        return paths
    tls_protocols: tuple[Literal["xhttp"], Literal["wss"]] = ("xhttp", "wss")
    for protocol in tls_protocols:
        # Keep transport paths stable across redeploys so existing links survive.
        prior = previous.get(protocol)
        path = prior.path if prior is not None and prior.host == domain else secrets.token_hex(8)
        paths.append(
            ProtocolPathIntent(
                id=f"{exit_id}-{protocol}",
                protocol=protocol,
                tls_sni=domain,
                host=domain,
                path=path,
            )
        )
    if hysteria2:
        paths.append(ProtocolPathIntent(id=f"{exit_id}-hysteria2", protocol="hysteria2", tls_sni=domain))
    return paths
