"""Fleet service use cases with injectable infrastructure adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, Self

from meridian.core.fleet import (
    ApiNodeLike,
    ApiUserLike,
    FleetInventory,
    FleetSources,
    FleetStatus,
    FleetTopology,
    TopologyRelay,
    build_fleet_inventory,
    build_fleet_status,
)
from meridian.core.models import CoreModel, ErrorModel
from meridian.core.server_allowances import ServerUsageList
from meridian.core.traffic import NodeTrafficEvidence

PanelErrorKind = Literal["auth", "system", "bug"]


class FleetPanelClient(Protocol):
    """Panel operations needed by fleet service use cases."""

    def __enter__(self) -> Self: ...

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> object: ...

    def ping(self) -> bool: ...

    def list_nodes(self) -> Sequence[ApiNodeLike]: ...

    def list_users(self) -> Sequence[ApiUserLike]: ...


PanelErrorClassifier = Callable[[Exception], PanelErrorKind]
RelayHealthChecker = Callable[[list[TopologyRelay]], Mapping[tuple[str, int], bool]]
NodeEvidenceReader = Callable[[], Mapping[str, NodeTrafficEvidence]]


class FleetStatusServiceResult(CoreModel):
    """Status result plus non-fatal collection warnings."""

    status: FleetStatus
    warnings: list[ErrorModel]


class FleetInventoryServiceResult(CoreModel):
    """Inventory result plus non-fatal collection warnings."""

    inventory: FleetInventory
    warnings: list[ErrorModel]


def _system_warning(
    code: str,
    message: str,
    *,
    hint: str = "",
    details: dict[str, object] | None = None,
) -> ErrorModel:
    """Build a retryable system warning for partial fleet data."""
    return ErrorModel(
        code=code,
        category="system",
        message=message,
        hint=hint,
        retryable=True,
        exit_code=3,
        details=details or {},
    )


def _handle_error(exc: Exception, classify_error: PanelErrorClassifier) -> None:
    """Reraise auth errors; keep system errors as partial observations."""
    if classify_error(exc) in ("auth", "bug"):
        raise exc


@dataclass
class _PanelObservation:
    """Live panel data gathered for one fleet read, plus partial-failure warnings."""

    sources: FleetSources
    healthy: bool = False
    nodes: list[ApiNodeLike] = field(default_factory=list)
    node_evidence: dict[str, NodeTrafficEvidence] = field(default_factory=dict)
    users: list[ApiUserLike] = field(default_factory=list)
    warnings: list[ErrorModel] = field(default_factory=list)
    server_usage: ServerUsageList | None = None


def _observe_panel(
    panel_client: FleetPanelClient,
    classify_error: PanelErrorClassifier,
    *,
    sources: FleetSources,
    unreachable: str,
    access_users: list[str] | None = None,
    with_users: bool,
    read_node_evidence: NodeEvidenceReader | None = None,
    read_server_usage: Callable[[], ServerUsageList] | None = None,
) -> _PanelObservation:
    """Ping the panel and list nodes (and users); auth and bug errors propagate."""
    observed = _PanelObservation(sources=sources)
    unavailable = {"panel": "unavailable", "nodes": "unavailable"} | ({"users": "unavailable"} if with_users else {})
    if read_server_usage is not None:
        unavailable["server_usage"] = "unavailable"

    def unreachable_warning(details: dict[str, object] | None = None) -> ErrorModel:
        return _system_warning(
            "MERIDIAN_PANEL_UNREACHABLE",
            unreachable,
            hint="Verify panel connectivity from the caller environment.",
            details=details,
        )

    try:
        with panel_client as panel:
            observed.healthy = panel.ping()
            if not observed.healthy:
                observed.warnings.append(unreachable_warning())
                observed.sources = observed.sources.model_copy(update=unavailable)
                return observed
            observed.sources = observed.sources.model_copy(update={"panel": "available"})
            try:
                observed.nodes = list(panel.list_nodes())
                if read_node_evidence is not None:
                    observed.node_evidence = dict(read_node_evidence())
                observed.sources = observed.sources.model_copy(update={"nodes": "available"})
            except Exception as exc:  # Protocol adapter — concrete exception type unknown
                _handle_error(exc, classify_error)
                observed.sources = observed.sources.model_copy(update={"nodes": "unavailable"})
                observed.warnings.append(
                    _system_warning(
                        "MERIDIAN_PANEL_NODES_UNAVAILABLE",
                        "Could not fetch node status from panel",
                        hint="Panel is reachable, but the node list API failed.",
                        details={"cause": type(exc).__name__},
                    )
                )
            if read_server_usage is not None:
                try:
                    observed.server_usage = read_server_usage()
                    observed.sources = observed.sources.model_copy(update={"server_usage": "available"})
                except Exception as exc:
                    _handle_error(exc, classify_error)
                    observed.sources = observed.sources.model_copy(update={"server_usage": "unavailable"})
                    observed.warnings.append(
                        _system_warning(
                            "MERIDIAN_SERVER_USAGE_UNAVAILABLE",
                            "Could not read server allowance usage",
                            hint="Retry fleet status when the control service is available.",
                        )
                    )
            if not with_users:
                return observed
            try:
                users = panel.list_users()
                if access_users is not None:
                    allowed_users = set(access_users)
                    users = [user for user in users if user.username in allowed_users]
                observed.users = list(users)
                observed.sources = observed.sources.model_copy(update={"users": "available"})
            except Exception as exc:  # Protocol adapter — concrete exception type unknown
                _handle_error(exc, classify_error)
                observed.sources = observed.sources.model_copy(update={"users": "unavailable"})
                observed.warnings.append(
                    _system_warning(
                        "MERIDIAN_PANEL_USERS_UNAVAILABLE",
                        "Could not fetch user status from panel",
                        hint="Panel is reachable, but the user list API failed.",
                        details={"cause": type(exc).__name__},
                    )
                )
    except Exception as exc:  # Protocol adapter — concrete exception type unknown
        _handle_error(exc, classify_error)
        observed.healthy = False
        observed.sources = observed.sources.model_copy(update=unavailable)
        observed.warnings.append(unreachable_warning({"cause": type(exc).__name__}))
    return observed


def collect_fleet_inventory(
    topology: FleetTopology,
    panel_client: FleetPanelClient,
    *,
    classify_error: PanelErrorClassifier,
) -> FleetInventoryServiceResult:
    """Collect redacted fleet inventory with live panel node observations."""
    panel = _observe_panel(
        panel_client,
        classify_error,
        sources=FleetSources(panel="unknown", nodes="not_requested", users="not_requested", relays="not_requested"),
        unreachable="Cannot reach panel API; live inventory status is unavailable",
        with_users=False,
    )
    return FleetInventoryServiceResult(
        inventory=build_fleet_inventory(
            topology, panel_healthy=panel.healthy, api_nodes=panel.nodes, sources=panel.sources
        ),
        warnings=panel.warnings,
    )


def collect_fleet_status(
    topology: FleetTopology,
    panel_client: FleetPanelClient,
    *,
    check_relays: RelayHealthChecker,
    classify_error: PanelErrorClassifier,
    read_node_evidence: NodeEvidenceReader | None = None,
    read_server_usage: Callable[[], ServerUsageList] | None = None,
) -> FleetStatusServiceResult:
    """Collect fleet health overview with partial-failure warnings."""
    relay_source: Literal["available", "not_requested"] = "available" if topology.relays else "not_requested"
    panel = _observe_panel(
        panel_client,
        classify_error,
        sources=FleetSources(panel="unknown", nodes="not_requested", users="not_requested", relays=relay_source),
        unreachable="Cannot reach panel API -- node and user data may be stale",
        access_users=topology.access_users,
        with_users=True,
        read_node_evidence=read_node_evidence,
        read_server_usage=read_server_usage,
    )
    sources = panel.sources
    warnings = panel.warnings

    try:
        relay_health = check_relays(topology.relays)
    except Exception as exc:  # Protocol adapter — concrete exception type unknown
        relay_health = {}
        sources = sources.model_copy(update={"relays": "unavailable"})
        warnings.append(
            _system_warning(
                "MERIDIAN_RELAYS_UNAVAILABLE",
                "Could not check relay health",
                hint="Verify relay connectivity from the caller environment.",
                details={"cause": type(exc).__name__},
            )
        )
    return FleetStatusServiceResult(
        status=build_fleet_status(
            topology,
            panel_healthy=panel.healthy,
            api_nodes=panel.nodes,
            api_users=panel.users,
            relay_health=relay_health,
            sources=sources,
            node_evidence=panel.node_evidence,
            server_usage=panel.server_usage,
        ),
        warnings=warnings,
    )
