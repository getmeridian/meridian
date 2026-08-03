"""Target resolution and deployment context for client-side verification."""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology, deployment_server_refs
from meridian.compiler.models import (
    ControlPlaneRuntimePayload,
    HostPayload,
    InboundPayload,
    NginxArtifactPayload,
    RealmHopPayload,
)
from meridian.config import is_ip
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.inputs import validate_hostname_value
from meridian.core.topology import ProtocolKind, ServerCapability, SetupIntent
from meridian.core.verification import (
    VerificationCheck,
    VerificationContext,
    VerificationFinding,
    build_verification_check,
)
from meridian.diagnostics.network import resolve_hostname
from meridian.resolve import detect_local_server_ip, detect_public_ip, is_local_keyword
from meridian.servers import ServerEntry, ServerRegistry


class VerificationTargetError(MeridianError):
    """A public verification target is missing, ambiguous, or invalid."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message, hint=hint, category="user")


class VerificationEvidenceUnavailableError(MeridianError):
    """Required target-selection evidence could not be collected."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message, hint=hint, category="system", retryable=True)


@dataclass(frozen=True)
class ResolvedVerificationTarget:
    """Network target selected without creating an SSH connection."""

    ip: str
    label: str
    domain: str = ""
    local: bool = False


@dataclass(frozen=True)
class VerificationHttpsRoute:
    """One public HTTPS route with distinct TLS and HTTP identities."""

    kind: Literal["managed", "camouflage", "generic"]
    port: int
    tls_sni: str = ""
    host_header: str = ""
    panel: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("HTTPS route port must be between 1 and 65535.")
        if self.panel and self.kind != "managed":
            raise ValueError("Only a managed HTTPS route can serve the panel.")


@dataclass(frozen=True)
class DeploymentVerificationContext:
    """Secret-free deployment facts needed by external checks."""

    kind: Literal["external", "legacy", "v4"] = "external"
    roles: tuple[str, ...] = ()
    protocols: tuple[ProtocolKind, ...] = ()
    reality_snis: tuple[str, ...] = ()
    reality_sni_ports: dict[str, int] = field(default_factory=dict)
    domains: tuple[str, ...] = ()
    domain_ports: dict[str, int] = field(default_factory=dict)
    internal_ports: dict[str, int] = field(default_factory=dict)
    public_tcp_ports: tuple[int, ...] = (443,)
    public_tls_ports: tuple[int, ...] = ()
    public_udp_ports: tuple[int, ...] = ()
    https_routes: tuple[VerificationHttpsRoute, ...] = ()
    endpoint_addresses: tuple[str, ...] = ()
    panel_secret_path: str = ""
    expected_egress_ip: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "endpoint_addresses",
            tuple(
                sorted({canonical_endpoint_address(address) for address in self.endpoint_addresses if address.strip()})
            ),
        )
        tls_protocols = {"reality", "xhttp", "wss"}
        if not self.public_tls_ports and (self.kind == "external" or tls_protocols.intersection(self.protocols)):
            object.__setattr__(self, "public_tls_ports", self.public_tcp_ports)
        if not set(self.public_tls_ports) <= set(self.public_tcp_ports):
            raise ValueError("Public TLS ports must also be public TCP ports.")

        default_port = self.public_tls_ports[0] if self.public_tls_ports else 0
        routes = set(self.https_routes)
        for domain in self.domains:
            if any(
                route.kind != "camouflage"
                and (route.host_header == domain or not route.host_header and route.tls_sni == domain)
                for route in routes
            ):
                continue
            port = self.domain_ports.get(domain, default_port)
            if port:
                routes.add(
                    VerificationHttpsRoute(
                        kind="generic" if self.kind == "external" else "managed",
                        port=port,
                        tls_sni=domain,
                        host_header=domain,
                    )
                )
        for sni in self.reality_snis:
            if any(route.kind == "camouflage" and route.tls_sni == sni for route in routes):
                continue
            port = self.reality_sni_ports.get(sni, default_port)
            if port:
                routes.add(VerificationHttpsRoute(kind="camouflage", port=port, tls_sni=sni))
        if not routes:
            routes.update(
                VerificationHttpsRoute(
                    kind="generic" if self.kind == "external" else "managed",
                    port=port,
                )
                for port in self.public_tls_ports
            )
        merged_routes: dict[tuple[str, int, str, str], VerificationHttpsRoute] = {}
        for route in routes:
            key = (route.kind, route.port, route.tls_sni, route.host_header)
            existing = merged_routes.get(key)
            if existing is None or route.panel:
                merged_routes[key] = route if existing is None else replace(existing, panel=True)
        ordered_routes = tuple(
            sorted(
                merged_routes.values(),
                key=lambda route: (route.port, route.kind, route.tls_sni, route.host_header),
            )
        )
        object.__setattr__(self, "https_routes", ordered_routes)

        reality_snis = set(self.reality_snis)
        domains = set(self.domains)
        reality_ports = dict(self.reality_sni_ports)
        domain_ports = dict(self.domain_ports)
        for route in ordered_routes:
            if route.kind == "camouflage" and route.tls_sni:
                reality_snis.add(route.tls_sni)
                reality_ports.setdefault(route.tls_sni, route.port)
            elif route.kind != "camouflage":
                domain = route.host_header or route.tls_sni
                if domain:
                    domains.add(domain)
                    domain_ports.setdefault(domain, route.port)
        object.__setattr__(self, "reality_snis", tuple(sorted(reality_snis)))
        object.__setattr__(self, "reality_sni_ports", dict(sorted(reality_ports.items())))
        object.__setattr__(self, "domains", tuple(sorted(domains)))
        object.__setattr__(self, "domain_ports", dict(sorted(domain_ports.items())))


def normalize_verification_timeout(value: float | str) -> float:
    """Parse the CLI/library timeout at the typed verification boundary."""
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise VerificationTargetError("Timeout must be a number between 1 and 30 seconds.") from exc
    if not math.isfinite(timeout) or not 1 <= timeout <= 30:
        raise VerificationTargetError("Timeout must be between 1 and 30 seconds.")
    return timeout


def canonical_endpoint_address(value: str) -> str:
    """Canonicalize IP spelling while preserving hostnames and empty values."""
    value = value.strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return value


def core_verification_context(
    deployment: DeploymentVerificationContext,
    *,
    domain: str = "",
    sni: str = "",
) -> VerificationContext:
    """Convert internal deployment facts into the stable public contract."""
    roles: list[ServerCapability] = []
    if any(role == "control" for role in deployment.roles):
        roles.append("panel")
    if any(role.startswith("exit") for role in deployment.roles):
        roles.append("exit")
    if any(role.startswith("relay") for role in deployment.roles):
        roles.append("relay")
    if any(role.startswith("gateway") for role in deployment.roles):
        roles.append("routing_gateway")
    return VerificationContext(
        mode="meridian" if deployment.kind != "external" else "generic",
        roles=roles,
        protocols=list(deployment.protocols),
        domain=domain or (deployment.domains[0] if deployment.domains else ""),
        sni=sni or (deployment.reality_snis[0] if deployment.reality_snis else ""),
    )


def resolve_verification_target(
    value: str,
    requested_server: str,
    registry: ServerRegistry,
    *,
    allow_domain: bool,
    timeout: float = 5,
) -> ResolvedVerificationTarget:
    """Resolve an external target with registry names taking precedence over DNS."""
    value = value.strip()
    requested_server = requested_server.strip()
    if value and requested_server:
        raise VerificationTargetError(
            "Choose either a target argument or --server, not both.",
            hint="Remove one target selector and retry.",
        )

    if value:
        if is_local_keyword(value):
            return _local_target()
        if is_ip(value):
            normalized = str(ipaddress.ip_address(value))
            return ResolvedVerificationTarget(ip=normalized, label=normalized)
        if not allow_domain:
            raise VerificationTargetError(
                f"Target {value!r} is not an IP address.",
                hint="Pass an IP address or select a saved server with --server NAME.",
            )
        try:
            domain = validate_hostname_value(value)
        except ValueError as exc:
            raise VerificationTargetError(str(exc)) from exc
        resolved_ip = resolve_domain(domain, timeout=timeout)
        if resolved_ip is None:
            raise VerificationEvidenceUnavailableError(
                f"DNS resolution for {domain!r} was unavailable.",
                hint="Check the local resolver and network access, then retry.",
            )
        if not resolved_ip:
            raise VerificationTargetError(
                f"Domain {domain!r} did not resolve.",
                hint="Check the name or pass the server IP directly.",
            )
        return ResolvedVerificationTarget(ip=resolved_ip, label=f"{domain} ({resolved_ip})", domain=domain)

    if requested_server:
        if is_local_keyword(requested_server):
            return _local_target()
        entry = registry.find(requested_server)
        if entry is not None:
            return _entry_target(entry)
        if is_ip(requested_server):
            normalized = str(ipaddress.ip_address(requested_server))
            return ResolvedVerificationTarget(ip=normalized, label=normalized)
        raise VerificationTargetError(
            f"Saved server {requested_server!r} was not found.",
            hint="Run `meridian server list` and choose an existing name.",
        )

    local_ip = detect_local_server_ip()
    if local_ip:
        return ResolvedVerificationTarget(ip=local_ip, label=local_ip)
    entries = registry.list()
    if len(entries) == 1:
        return _entry_target(entries[0])
    if not entries:
        raise VerificationTargetError(
            "No verification target was provided.",
            hint="Pass an IP/domain or save a server first with `meridian server add`.",
        )
    names = ", ".join(entry.name or entry.host for entry in entries)
    raise VerificationTargetError(
        "Multiple saved servers are available.",
        hint=f"Pass --server NAME. Available: {names}",
    )


def resolve_domain(domain: str, *, timeout: float = 5) -> str | None:
    """Resolve a hostname within the command timeout, preferring IPv4."""
    return resolve_hostname(domain, timeout=timeout)


def deployment_verification_context(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    target_ip: str,
) -> DeploymentVerificationContext:
    """Return V4 facts first, falling back to legacy node metadata."""
    target_ip = canonical_endpoint_address(target_ip)
    if cluster.topology_intent is not None:
        context = _v4_context(cluster, registry, target_ip)
        if context.kind == "v4":
            return context
    node = next(
        (candidate for candidate in cluster.nodes if canonical_endpoint_address(candidate.ip) == target_ip), None
    )
    if node is None:
        relay = next(
            (candidate for candidate in cluster.relays if canonical_endpoint_address(candidate.ip) == target_ip),
            None,
        )
        exit_node = (
            next(
                (
                    candidate
                    for candidate in cluster.nodes
                    if canonical_endpoint_address(candidate.ip) == canonical_endpoint_address(relay.exit_node_ip)
                ),
                None,
            )
            if relay is not None
            else None
        )
        if relay is not None and exit_node is not None:
            exit_context = deployment_verification_context(cluster, registry, exit_node.ip)
            relay_sni = relay.sni or exit_node.sni
            advertised_protocols: list[ProtocolKind] = ["reality"]
            relay_routes = [VerificationHttpsRoute(kind="camouflage", port=relay.port, tls_sni=relay_sni)]
            if "xhttp" in relay.host_uuids:
                advertised_protocols.append("xhttp")
                relay_routes.append(
                    VerificationHttpsRoute(
                        kind="managed",
                        port=relay.port,
                        tls_sni=relay_sni,
                        host_header=relay_sni,
                    )
                )
            return replace(
                exit_context,
                roles=("relay",),
                protocols=tuple(advertised_protocols),
                reality_snis=(relay_sni,) if relay_sni else (),
                reality_sni_ports={relay_sni: relay.port} if relay_sni else {},
                domains=(relay_sni,) if "xhttp" in relay.host_uuids and relay_sni else (),
                domain_ports={relay_sni: relay.port} if "xhttp" in relay.host_uuids and relay_sni else {},
                public_tcp_ports=(relay.port,),
                public_tls_ports=(relay.port,),
                public_udp_ports=(),
                https_routes=tuple(relay_routes) if relay_sni else (),
                internal_ports={},
                endpoint_addresses=(canonical_endpoint_address(relay.ip),),
                panel_secret_path="",
            )
        return DeploymentVerificationContext(endpoint_addresses=(target_ip,))

    from meridian.core.deploy_planning import compute_deploy_ports

    ports = compute_deploy_ports(node.ip)
    internal = {"xhttp": ports.xhttp_port, "reality": ports.reality_port}
    if node.domain:
        internal["wss"] = ports.wss_port
    endpoint_addresses = {canonical_endpoint_address(node.ip)}
    if node.domain:
        endpoint_addresses.add(node.domain)
    for relay in cluster.relays:
        if not relay.exit_node_ip or canonical_endpoint_address(relay.exit_node_ip) == canonical_endpoint_address(
            node.ip
        ):
            endpoint_addresses.add(canonical_endpoint_address(relay.ip))
    protocols: list[ProtocolKind] = []
    if node.reality_public_key or node.sni:
        protocols.append("reality")
    if node.xhttp_path:
        protocols.append("xhttp")
    if node.domain and node.ws_path:
        protocols.append("wss")
    if node.hysteria2:
        protocols.append("hysteria2")
    https_routes = [
        VerificationHttpsRoute(
            kind="managed",
            port=443,
            tls_sni=node.domain,
            host_header=node.domain,
            panel=node.is_panel_host,
        )
    ]
    if node.sni:
        https_routes.append(VerificationHttpsRoute(kind="camouflage", port=443, tls_sni=node.sni))
    return DeploymentVerificationContext(
        kind="legacy",
        roles=("exit",),
        protocols=tuple(protocols),
        reality_snis=(node.sni,) if node.sni else (),
        reality_sni_ports={node.sni: 443} if node.sni else {},
        domains=(node.domain,) if node.domain else (),
        domain_ports={node.domain: 443} if node.domain else {},
        internal_ports=internal,
        public_tcp_ports=(443,),
        public_tls_ports=(443,),
        public_udp_ports=(443,) if node.hysteria2 else (),
        https_routes=tuple(https_routes),
        endpoint_addresses=tuple(sorted(endpoint_addresses)),
        panel_secret_path=cluster.panel.secret_path if node.is_panel_host else "",
        expected_egress_ip="" if node.warp else canonical_endpoint_address(node.ip),
    )


def resolve_deployment_verification_context(
    registry: ServerRegistry,
    target_ip: str,
    *,
    cluster: ClusterConfig | None = None,
    allow_external_fallback: bool = False,
) -> tuple[ClusterConfig, DeploymentVerificationContext, VerificationCheck | None]:
    """Load deployment facts, optionally preserving an explicit external probe."""
    effective_cluster = cluster
    try:
        effective_cluster = effective_cluster if effective_cluster is not None else ClusterConfig.load()
        deployment = deployment_verification_context(effective_cluster, registry, target_ip)
        return effective_cluster, deployment, None
    except (LocalStateError, ValueError) as exc:
        if not allow_external_fallback:
            if isinstance(exc, LocalStateError):
                raise
            raise LocalStateError(
                f"Cannot compile the saved V4 topology: {exc}",
                hint="Review the topology with `meridian setup`, then retry.",
            ) from exc
        hint = exc.hint if isinstance(exc, LocalStateError) else "Review the saved topology and retry."
        check = build_verification_check(
            "deployment_context",
            "Deployment context",
            [
                VerificationFinding(
                    code="LOCAL_CONTEXT_UNAVAILABLE",
                    status="skipped",
                    message=str(exc),
                    remediation=hint,
                )
            ],
        )
        return (
            effective_cluster or ClusterConfig(),
            DeploymentVerificationContext(endpoint_addresses=(target_ip,)),
            check,
        )


def _v4_context(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    target_ip: str,
) -> DeploymentVerificationContext:
    intent = cluster.topology_intent
    if intent is None:
        return DeploymentVerificationContext(endpoint_addresses=(target_ip,))
    addresses = _server_addresses(registry, deployment_server_refs(intent))
    matching_refs = {server_ref for server_ref, address in addresses.items() if address == target_ip}
    if not matching_refs:
        return DeploymentVerificationContext(endpoint_addresses=(target_ip,))

    plan = compile_topology(intent)
    workload_servers = {exit_.id: exit_.server_ref for exit_ in intent.exits}
    workload_servers.update({gateway.id: gateway.server_ref for gateway in intent.routing_gateways})
    target_exit_ids = {exit_.id for exit_ in intent.exits if exit_.server_ref in matching_refs}
    relay_exit_by_id = {relay.id: relay.exit_ref for relay in intent.transparent_relays}

    roles: set[str] = set()
    if intent.control.server_ref in matching_refs:
        roles.add("control")
    roles.update(f"exit:{exit_.id}" for exit_ in intent.exits if exit_.server_ref in matching_refs)
    roles.update(f"gateway:{gateway.id}" for gateway in intent.routing_gateways if gateway.server_ref in matching_refs)
    for relay in intent.transparent_relays:
        for index, server_ref in enumerate(relay.hop_server_refs):
            if server_ref in matching_refs:
                roles.add(f"relay:{relay.id}:hop:{index}")

    reality_snis: set[str] = set()
    reality_sni_ports: dict[str, int] = {}
    domains: set[str] = set()
    domain_ports: dict[str, int] = {}
    internal_ports: dict[str, int] = {}
    public_tcp_ports: set[int] = set()
    public_tls_ports: set[int] = set()
    public_udp_ports: set[int] = set()
    https_routes: set[VerificationHttpsRoute] = set()
    endpoint_addresses: set[str] = {target_ip}
    protocols: set[ProtocolKind] = set()

    for resource in plan.resources:
        payload = resource.payload
        if isinstance(payload, ControlPlaneRuntimePayload) and payload.server_ref in matching_refs:
            internal_ports["control/https"] = payload.internal_https_port
            continue
        if isinstance(payload, InboundPayload):
            server_ref = workload_servers.get(payload.workload_ref, "")
            if server_ref not in matching_refs:
                continue
            allocation = cluster.allocations.get(resource.logical_id)
            listen_port = allocation.port if allocation and allocation.port else payload.listen_port
            if listen_port and payload.purpose == "bridge":
                internal_ports[f"bridge/{resource.logical_id}"] = listen_port
            elif listen_port and listen_port != payload.public_port:
                internal_ports[f"{payload.workload_ref}/{payload.protocol}"] = listen_port
            continue
        if isinstance(payload, NginxArtifactPayload) and payload.server_ref in matching_refs:
            if payload.layer == "http":
                internal_ports[f"nginx/{resource.logical_id}"] = payload.listener_port
            continue
        if isinstance(payload, RealmHopPayload) and payload.server_ref in matching_refs:
            if payload.advertised:
                public_tcp_ports.add(payload.listen_port)
                public_tls_ports.add(payload.listen_port)
            else:
                internal_ports[f"realm/{payload.chain_ref}/hop/{payload.hop_index}"] = payload.listen_port
            continue
        if not isinstance(payload, HostPayload):
            continue

        endpoint_ip = addresses.get(payload.address_server_ref, "")
        endpoint = canonical_endpoint_address(payload.address or endpoint_ip)
        owner_is_related = (
            payload.owner_ref in target_exit_ids or relay_exit_by_id.get(payload.owner_ref) in target_exit_ids
        )
        is_public = payload.advertised and not payload.is_hidden
        if is_public and (payload.address_server_ref in matching_refs or owner_is_related):
            if endpoint:
                endpoint_addresses.add(endpoint)
            if endpoint_ip:
                endpoint_addresses.add(endpoint_ip)
        if payload.address_server_ref not in matching_refs:
            continue
        protocols.add(payload.protocol)
        if not is_public:
            continue
        if payload.protocol == "hysteria2":
            public_udp_ports.add(payload.public_port)
        else:
            public_tcp_ports.add(payload.public_port)
            public_tls_ports.add(payload.public_port)
        if payload.protocol == "reality" and payload.sni:
            reality_snis.add(payload.sni)
            reality_sni_ports[payload.sni] = payload.public_port
            https_routes.add(VerificationHttpsRoute(kind="camouflage", port=payload.public_port, tls_sni=payload.sni))
        elif payload.protocol != "hysteria2":
            tls_sni = payload.sni or payload.host
            host_header = payload.host or payload.sni
            https_routes.add(
                VerificationHttpsRoute(
                    kind="managed",
                    port=payload.public_port,
                    tls_sni=tls_sni,
                    host_header=host_header,
                )
            )
            if host_header:
                domains.add(host_header)
                domain_ports[host_header] = payload.public_port

    if intent.control.server_ref in matching_refs:
        public_tcp_ports.add(443)
        public_tls_ports.add(443)
        control_route = VerificationHttpsRoute(
            kind="managed",
            port=443,
            tls_sni=intent.control.public_hostname,
            host_header=intent.control.public_hostname,
            panel=True,
        )
        existing_route = next(
            (
                route
                for route in https_routes
                if (route.kind, route.port, route.tls_sni, route.host_header)
                == (control_route.kind, control_route.port, control_route.tls_sni, control_route.host_header)
            ),
            None,
        )
        if existing_route is not None:
            https_routes.remove(existing_route)
        https_routes.add(control_route)
        if intent.control.public_hostname:
            domains.add(intent.control.public_hostname)
            domain_ports[intent.control.public_hostname] = 443
            endpoint_addresses.add(intent.control.public_hostname)

    expected_egress_ip = _v4_expected_egress_ip(intent, addresses, matching_refs)
    return DeploymentVerificationContext(
        kind="v4",
        roles=tuple(sorted(roles)),
        protocols=tuple(sorted(protocols)),
        reality_snis=tuple(sorted(reality_snis)),
        reality_sni_ports=dict(sorted(reality_sni_ports.items())),
        domains=tuple(sorted(domains)),
        domain_ports=dict(sorted(domain_ports.items())),
        internal_ports=dict(sorted(internal_ports.items())),
        public_tcp_ports=tuple(sorted(public_tcp_ports)),
        public_tls_ports=tuple(sorted(public_tls_ports)),
        public_udp_ports=tuple(sorted(public_udp_ports)),
        https_routes=tuple(https_routes),
        endpoint_addresses=tuple(sorted(endpoint_addresses)),
        panel_secret_path=cluster.panel.secret_path if intent.control.server_ref in matching_refs else "",
        expected_egress_ip=expected_egress_ip,
    )


def _v4_expected_egress_ip(
    intent: SetupIntent,
    addresses: dict[str, str],
    matching_refs: set[str],
) -> str:
    """Return one certain direct egress, leaving gateways/WARP explicitly unknown."""
    candidates: set[str] = set()
    unknown = False
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    for exit_ in intent.exits:
        if exit_.server_ref in matching_refs:
            if exit_.warp:
                unknown = True
            else:
                candidates.add(addresses.get(exit_.server_ref, ""))
    for relay in intent.transparent_relays:
        if not relay.hop_server_refs or relay.hop_server_refs[0] not in matching_refs:
            continue
        exit_ = exits[relay.exit_ref]
        if exit_.warp:
            unknown = True
        else:
            candidates.add(addresses.get(exit_.server_ref, ""))
    if any(gateway.server_ref in matching_refs for gateway in intent.routing_gateways):
        unknown = True
    candidates.discard("")
    return next(iter(candidates)) if len(candidates) == 1 and not unknown else ""


def _server_addresses(registry: ServerRegistry, server_refs: Iterable[str]) -> dict[str, str]:
    entries = registry.list()
    addresses: dict[str, str] = {}
    for server_ref in server_refs:
        entry = next(
            (candidate for candidate in entries if server_ref in {candidate.id, candidate.name, candidate.host}),
            None,
        )
        if entry is not None:
            addresses[server_ref] = canonical_endpoint_address(entry.host)
        elif is_ip(server_ref):
            addresses[server_ref] = str(ipaddress.ip_address(server_ref))
        else:
            raise LocalStateError(
                f"V4 topology references missing saved server {server_ref!r}.",
                hint="Repair the server shelf before running deployment-aware verification.",
            )
    return addresses


def _local_target() -> ResolvedVerificationTarget:
    ip = detect_local_server_ip() or detect_public_ip()
    if not ip:
        raise VerificationTargetError(
            "Could not determine this machine's public IP.",
            hint="Pass the public IP explicitly.",
        )
    normalized = str(ipaddress.ip_address(ip))
    return ResolvedVerificationTarget(ip=normalized, label=normalized, local=True)


def _entry_target(entry: ServerEntry) -> ResolvedVerificationTarget:
    normalized = str(ipaddress.ip_address(entry.host))
    label = f"{entry.name} ({normalized})" if entry.name else normalized
    return ResolvedVerificationTarget(ip=normalized, label=label)
