"""Read attention from one locked view of aggregate accounting and agent report state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from meridian.control.models import NodeStatus, UserRecord
from meridian.control.server_allowances import read_server_usage
from meridian.control.store import Store
from meridian.core.attention import AttentionList, AttentionThresholds, derive_attention


def read_attention(
    store: Store,
    read_users: Callable[[], list[UserRecord]],
    read_nodes: Callable[[], list[NodeStatus]],
    thresholds: AttentionThresholds,
    now: str,
) -> AttentionList:
    with store.lock:
        nodes = read_nodes()
        moment = datetime.fromisoformat(now)
        unhealthy = {
            node.node_id
            for node in nodes
            if node.runtime_available(moment) and node.runtime is not None and node.runtime.degraded
        }
        return derive_attention(
            read_users(),
            nodes,
            thresholds,
            now,
            unhealthy_nodes=unhealthy,
            server_usage=read_server_usage(store, now).servers,
        )
