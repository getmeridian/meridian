"""Applied-state snapshot management and hybrid declarative-imperative sync.

Imperative commands (``client add``, ``node add``, ``relay deploy``) modify
panel state outside the reconciler. If the user also manages the same
resource type declaratively (``desired_clients``, ``desired_nodes``,
``desired_relays`` in cluster.yml), the imperative mutation must be
mirrored into both the desired list **and** the applied-state snapshot.
Without this mirror, the next ``meridian plan`` classifies the
freshly-added resource as drift and proposes to remove it.

Contract: only sync when the user has already opted into declarative
for the relevant resource type (the matching ``desired_*`` attribute is not
None). A None value means "this category is unmanaged declaratively" —
leaving imperative-only workflows untouched for users who never wrote
``desired_*`` into cluster.yml.
"""

from __future__ import annotations

from typing import Any, Literal

from meridian.cluster import ClusterConfig, DesiredNode, DesiredRelay, NodeEntry, RelayEntry

AppliedField = Literal["clients", "nodes", "relays"]


# ---------------------------------------------------------------------------
# Low-level snapshot access
# ---------------------------------------------------------------------------


def load_applied_snapshot(cluster: ClusterConfig, field_name: AppliedField) -> set[str] | None:
    """Read a reconciler applied-state snapshot from ``cluster.applied_state``.

    Returns a set of strings or None. None means "no history" -- the caller
    treats this as the conservative drift classification (safest default).

    An empty list in applied_state means "managed and converged to zero"
    which is semantically distinct from None (no history). Empty lists
    return an empty set so compute_plan can distinguish them.
    """
    snap = getattr(cluster.applied_state, field_name, None)
    if snap is None:
        return None
    if not isinstance(snap, list):
        return None
    clean: set[str] = set()
    for item in snap:
        if isinstance(item, str):
            clean.add(item)
    # Empty list -> empty set (not None). This preserves the "managed,
    # converged to zero" semantics -- compute_plan needs this to distinguish
    # from "no history" (None).
    return clean


def _applied_snapshot_mirror_add(cluster: ClusterConfig, field_name: AppliedField, entry: Any) -> None:
    """Mirror a successful imperative add into the applied snapshot.

    Without this, compute_plan classifies the imperative addition as drift
    (``from_extras=True``) on the NEXT plan after the user deletes the resource
    from desired_*, and ``apply --yes`` silently skips the deliberate removal.
    The applied snapshot must reflect "panel state after the last reconciled
    add/remove operation we executed" — and `meridian client add` IS such an op.
    """
    snap = getattr(cluster.applied_state, field_name, None)
    if not isinstance(snap, list):
        snap = []
    if entry not in snap:
        snap.append(entry)
    setattr(cluster.applied_state, field_name, snap)


def _applied_snapshot_mirror_remove(cluster: ClusterConfig, field_name: AppliedField, entry: Any) -> None:
    snap = getattr(cluster.applied_state, field_name, None)
    if not isinstance(snap, list):
        return
    setattr(cluster.applied_state, field_name, [e for e in snap if e != entry])


# ---------------------------------------------------------------------------
# Hybrid sync helpers — one per resource type × direction
# ---------------------------------------------------------------------------


def hybrid_sync_desired_clients_add(cluster: ClusterConfig, name: str) -> None:
    if cluster.desired_clients is None:
        return
    if name in cluster.desired_clients:
        return
    cluster.desired_clients.append(name)
    _applied_snapshot_mirror_add(cluster, "clients", name)
    cluster.save()


def hybrid_sync_desired_clients_remove(cluster: ClusterConfig, name: str) -> None:
    if cluster.desired_clients is None:
        return
    if name not in cluster.desired_clients:
        return
    cluster.desired_clients = [c for c in cluster.desired_clients if c != name]
    _applied_snapshot_mirror_remove(cluster, "clients", name)
    cluster.save()


def hybrid_sync_desired_nodes_add(cluster: ClusterConfig, node: NodeEntry, ssh_user: str, ssh_port: int) -> None:
    if cluster.desired_nodes is None:
        return
    if any(d.host == node.ip for d in cluster.desired_nodes):
        return
    cluster.desired_nodes.append(
        DesiredNode(
            host=node.ip,
            name=node.name,
            ssh_user=ssh_user,
            ssh_port=ssh_port,
            domain=node.domain,
            sni=node.sni,
            warp=node.warp,
        )
    )
    _applied_snapshot_mirror_add(cluster, "nodes", node.ip)
    cluster.save()


def hybrid_sync_desired_nodes_update(cluster: ClusterConfig, node: NodeEntry, old_name: str = "") -> None:
    """Mirror an in-place node metadata change into desired_nodes (if managed).

    If ``old_name`` is provided and the node was renamed, also rewrite any
    matching ``desired_relays[].exit_node`` references that pointed at the
    old name. Without this fix-up an imperative node rename would leave
    stale references that later resolve to nothing (apply.py refuses to
    delete an old relay when the new exit_node cannot be resolved).
    """
    saved = False
    if cluster.desired_nodes is not None:
        for d in cluster.desired_nodes:
            if d.host == node.ip:
                d.name = node.name
                d.sni = node.sni
                d.domain = node.domain
                d.warp = node.warp
                saved = True
                break

    if old_name and old_name != node.name and cluster.desired_relays is not None:
        for r in cluster.desired_relays:
            if r.exit_node == old_name:
                r.exit_node = node.name
                saved = True

    if saved:
        cluster.save()


def hybrid_sync_desired_nodes_remove(cluster: ClusterConfig, node_ip: str) -> None:
    if cluster.desired_nodes is None:
        return
    if not any(d.host == node_ip for d in cluster.desired_nodes):
        return
    cluster.desired_nodes = [d for d in cluster.desired_nodes if d.host != node_ip]
    _applied_snapshot_mirror_remove(cluster, "nodes", node_ip)
    cluster.save()


def hybrid_sync_desired_relays_add(cluster: ClusterConfig, relay: RelayEntry, exit_node_ref: str) -> None:
    if cluster.desired_relays is None:
        return
    if any(d.host == relay.ip for d in cluster.desired_relays):
        return
    cluster.desired_relays.append(
        DesiredRelay(
            host=relay.ip,
            name=relay.name,
            exit_node=exit_node_ref,
            sni=relay.sni,
            ssh_user=relay.ssh_user,
            ssh_port=relay.ssh_port,
        )
    )
    _applied_snapshot_mirror_add(cluster, "relays", relay.ip)
    cluster.save()


def hybrid_sync_desired_relays_remove(cluster: ClusterConfig, relay_ip: str) -> None:
    if cluster.desired_relays is None:
        return
    if not any(d.host == relay_ip for d in cluster.desired_relays):
        return
    cluster.desired_relays = [d for d in cluster.desired_relays if d.host != relay_ip]
    _applied_snapshot_mirror_remove(cluster, "relays", relay_ip)
    cluster.save()
