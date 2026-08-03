"""Pure diff engine — compare desired and actual state to produce a plan.

``compute_plan()`` is a pure function: no I/O, no side effects, fully
unit-testable. It takes two state snapshots and returns a list of typed
actions needed to converge actual → desired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from meridian.reconciler.state import (
    ActualNodeState,
    ActualRelayState,
    ActualState,
    DesiredNodeState,
    DesiredRelayState,
    DesiredState,
)


class PlanActionKind(str, Enum):
    """Types of reconciliation actions."""

    ADD_NODE = "add_node"
    UPDATE_NODE = "update_node"
    REMOVE_NODE = "remove_node"
    ADD_CLIENT = "add_client"
    REMOVE_CLIENT = "remove_client"
    ADD_RELAY = "add_relay"
    UPDATE_RELAY = "update_relay"
    REMOVE_RELAY = "remove_relay"
    ADD_SUBSCRIPTION_PAGE = "add_subscription_page"
    REMOVE_SUBSCRIPTION_PAGE = "remove_subscription_page"


@dataclass(frozen=True)
class ActionChange:
    """A structured before/after field change for update actions."""

    field: str
    before: str
    after: str

    def detail(self) -> str:
        """Human-readable change description for text plan output."""
        return f"{self.field}: {self.before} → {self.after}"


@dataclass
class PlanAction:
    """A single reconciliation action."""

    kind: PlanActionKind
    target: str  # identifier (IP, username, name)
    detail: str = ""  # human-readable description
    destructive: bool = False  # requires --yes confirmation
    # ``from_extras`` marks REMOVE_* actions caused by the resource existing in
    # actual state but missing from the desired declaration (i.e. drift). The
    # apply command's ``--prune-extras`` flag uses this tag to decide whether
    # to keep, prompt, or auto-remove. Non-REMOVE actions and REMOVE actions
    # that come from explicit `desired_*` removal stay False.
    from_extras: bool = False
    changes: list[ActionChange] = field(default_factory=list)

    @property
    def symbol(self) -> str:
        """Terraform-style symbol for display."""
        if self.kind.name.startswith("ADD"):
            return "+"
        if self.kind.name.startswith("REMOVE"):
            return "-"
        if self.kind.name.startswith("UPDATE"):
            return "~"
        return "~"


@dataclass
class Plan:
    """A reconciliation plan — the diff between desired and actual state."""

    actions: list[PlanAction] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """No changes needed — already converged."""
        return len(self.actions) == 0

    @property
    def has_destructive(self) -> bool:
        """Plan contains destructive actions (removals)."""
        return any(a.destructive for a in self.actions)

    @property
    def adds(self) -> list[PlanAction]:
        return [a for a in self.actions if a.symbol == "+"]

    @property
    def updates(self) -> list[PlanAction]:
        return [a for a in self.actions if a.symbol == "~"]

    @property
    def removes(self) -> list[PlanAction]:
        return [a for a in self.actions if a.symbol == "-"]

    def summary(self) -> str:
        """One-line summary: "Plan: 2 to add, 1 to update, 1 to remove"."""
        add_count = len(self.adds)
        update_count = len(self.updates)
        remove_count = len(self.removes)
        if self.is_empty:
            return "No changes. Infrastructure is up to date."
        parts = []
        if add_count:
            parts.append(f"{add_count} to add")
        if update_count:
            parts.append(f"{update_count} to update")
        if remove_count:
            parts.append(f"{remove_count} to remove")
        return f"Plan: {', '.join(parts)}."


def _node_changes(desired: DesiredNodeState, actual: ActualNodeState) -> list[ActionChange]:
    """Compare attributes of a matching node and return list of change descriptions.

    Empty desired values mean "not specified, keep current" — not "clear".
    Only non-empty desired values trigger drift detection.
    """
    changes: list[ActionChange] = []
    if desired.name and desired.name != actual.name:
        changes.append(ActionChange("name", actual.name or "(none)", desired.name))
    if desired.sni and desired.sni != actual.sni:
        changes.append(ActionChange("sni", actual.sni or "(default)", desired.sni))
    if desired.domain and desired.domain != actual.domain:
        changes.append(ActionChange("domain", actual.domain or "(none)", desired.domain))
    # warp uses None as "not specified — keep current". Only diff when explicitly set.
    if desired.warp is not None and desired.warp != actual.warp:
        changes.append(ActionChange("warp", str(actual.warp), str(desired.warp)))
    return changes


def _relay_changes(
    desired: DesiredRelayState,
    actual: ActualRelayState,
    node_name_to_ip: dict[str, str] | None = None,
) -> list[ActionChange]:
    """Compare attributes of a matching relay."""
    changes: list[ActionChange] = []
    if desired.name and desired.name != actual.name:
        changes.append(ActionChange("name", actual.name or "(none)", desired.name))
    if desired.sni and desired.sni != actual.sni:
        changes.append(ActionChange("sni", actual.sni or "(default)", desired.sni))
    if desired.exit_node:
        # Resolve node name to IP if needed
        resolved_exit = desired.exit_node
        if node_name_to_ip and resolved_exit in node_name_to_ip:
            resolved_exit = node_name_to_ip[resolved_exit]
        if resolved_exit != actual.exit_node_ip:
            changes.append(ActionChange("exit_node", actual.exit_node_ip or "(none)", desired.exit_node))
    return changes


def compute_plan(
    desired: DesiredState,
    actual: ActualState,
    *,
    applied_clients: set[str] | None = None,
    applied_node_hosts: set[str] | None = None,
    applied_relay_hosts: set[str] | None = None,
) -> Plan:
    """Compute the reconciliation plan.

    Compares desired state with actual state and returns typed actions
    needed to converge. Pure function — no I/O.

    Resource types with ``manage_*=False`` are skipped entirely — an
    empty desired list means "don't touch", not "delete everything".

    Action ordering: nodes → relays → clients → subscription page.
    """
    actions: list[PlanAction] = []

    # Build node name → IP map for relay exit_node resolution.
    # WARNING: duplicate names overwrite earlier entries. The cluster
    # validator catches duplicate hosts but not duplicate names — a name
    # collision here would silently resolve relay exit_node to the wrong
    # node IP. Consider adding a duplicate-name check to ClusterConfig.validate().
    node_name_to_ip: dict[str, str] = {}
    for actual_node in actual.nodes:
        if actual_node.name:
            node_name_to_ip[actual_node.name] = actual_node.host
    for desired_node in desired.nodes:
        if desired_node.name:
            node_name_to_ip[desired_node.name] = desired_node.host

    # --- Nodes ---
    if desired.manage_nodes:
        actual_nodes_by_host = {n.host: n for n in actual.nodes}
        desired_node_hosts = {n.host for n in desired.nodes}

        for d_node in desired.nodes:
            existing = actual_nodes_by_host.get(d_node.host)
            if existing is None:
                actions.append(
                    PlanAction(
                        kind=PlanActionKind.ADD_NODE,
                        target=d_node.host,
                        detail=f"provision node {d_node.name or d_node.host}",
                    )
                )
            else:
                changes = _node_changes(d_node, existing)
                if changes:
                    actions.append(
                        PlanAction(
                            kind=PlanActionKind.UPDATE_NODE,
                            target=d_node.host,
                            detail=f"redeploy node {d_node.name or d_node.host}: "
                            f"{', '.join(change.detail() for change in changes)}",
                            changes=changes,
                        )
                    )

        for a_node in actual.nodes:
            if a_node.host not in desired_node_hosts:
                # Never plan removal of the panel host — use teardown instead
                if a_node.is_panel_host:
                    continue
                was_in_applied = applied_node_hosts is not None and a_node.host in applied_node_hosts
                actions.append(
                    PlanAction(
                        kind=PlanActionKind.REMOVE_NODE,
                        target=a_node.host,
                        detail=f"deregister node {a_node.name or a_node.host}",
                        destructive=True,
                        from_extras=not was_in_applied,
                    )
                )

    # --- Relays ---
    if desired.manage_relays:
        actual_relays_by_host = {r.host: r for r in actual.relays}
        desired_relay_hosts = {r.host for r in desired.relays}

        for d_relay in desired.relays:
            existing_relay = actual_relays_by_host.get(d_relay.host)
            if existing_relay is None:
                actions.append(
                    PlanAction(
                        kind=PlanActionKind.ADD_RELAY,
                        target=d_relay.host,
                        detail=f"deploy relay {d_relay.name or d_relay.host} → {d_relay.exit_node}",
                    )
                )
            else:
                changes = _relay_changes(d_relay, existing_relay, node_name_to_ip)
                if changes:
                    actions.append(
                        PlanAction(
                            kind=PlanActionKind.UPDATE_RELAY,
                            target=d_relay.host,
                            detail=f"update relay {d_relay.name or d_relay.host}: "
                            f"{', '.join(change.detail() for change in changes)}",
                            destructive=True,  # implemented as delete + recreate
                            changes=changes,
                        )
                    )

        for a_relay in actual.relays:
            if a_relay.host not in desired_relay_hosts:
                was_in_applied = applied_relay_hosts is not None and a_relay.host in applied_relay_hosts
                actions.append(
                    PlanAction(
                        kind=PlanActionKind.REMOVE_RELAY,
                        target=a_relay.host,
                        detail=f"remove relay {a_relay.name or a_relay.host}",
                        destructive=True,
                        from_extras=not was_in_applied,
                    )
                )

    # --- Clients ---
    # Note: client comparison is set-based (username only). If a client's
    # attributes change (traffic limit, squad membership, etc.), the plan
    # will not detect the drift. This is a known design limitation — the
    # desired_clients schema is a list of usernames, not full user objects.
    # A future UPDATE_CLIENT action kind would require extending the schema.
    if desired.manage_clients:
        actual_clients = set(actual.clients)
        desired_clients_set = set(desired.clients)

        for client in sorted(desired_clients_set - actual_clients):
            actions.append(
                PlanAction(
                    kind=PlanActionKind.ADD_CLIENT,
                    target=client,
                    detail=f"create client {client}",
                )
            )

        for client in sorted(actual_clients - desired_clients_set):
            was_in_applied = applied_clients is not None and client in applied_clients
            actions.append(
                PlanAction(
                    kind=PlanActionKind.REMOVE_CLIENT,
                    target=client,
                    detail=f"delete client {client}",
                    destructive=True,
                    from_extras=not was_in_applied,
                )
            )

    # --- Subscription page (only if explicitly declared in cluster.yml) ---
    if desired.manage_subscription_page and desired.subscription_page_enabled and not actual.subscription_page_running:
        actions.append(
            PlanAction(
                kind=PlanActionKind.ADD_SUBSCRIPTION_PAGE,
                target="subscription-page",
                detail="deploy subscription page container",
            )
        )
    elif (
        desired.manage_subscription_page and not desired.subscription_page_enabled and actual.subscription_page_running
    ):
        actions.append(
            PlanAction(
                kind=PlanActionKind.REMOVE_SUBSCRIPTION_PAGE,
                target="subscription-page",
                detail="remove subscription page container",
                destructive=True,
            )
        )

    return Plan(actions=actions)
