"""The control service's checkpointed bootstrap driver and the subscription check verification shares."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import ControlServicePayload
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.xray_client import is_supported_proxy_outbound, parse_xray_subscription, proxy_outbounds


@dataclass(frozen=True)
class ControlServiceDriverContext:
    """Late-bound control bootstrap operations owned by setup runtime."""

    ready: Callable[[ResourceAction], bool]
    bootstrap: Callable[[ResourceAction], None]


class ControlServiceDriver:
    """Checkpoint the control-service bootstrap and require a responsive service afterward."""

    def __init__(self, context: ControlServiceDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        _control_payload(action)
        ready = self.context.ready(action)
        return _probe_observation(
            action,
            passed=ready,
            remote_id=(binding.remote_id if binding is not None else action.resource.logical_id),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        _control_payload(action)
        try:
            self.context.bootstrap(action)
        except UnknownResourceOutcome:
            raise
        except Exception as exc:
            if _exception_has_timeout(exc):
                raise UnknownResourceOutcome(
                    f"Timed out while bootstrapping {action.resource.logical_id}; observation is required."
                ) from exc
            raise
        return ResourceApplyReceipt(remote_id=action.resource.logical_id)


def xray_subscription_is_valid(document: Any) -> bool:
    """Require a canonical Xray document with at least one managed proxy."""
    content = getattr(document, "content", None)
    if not getattr(document, "url", "") or not isinstance(content, str) or not content.strip():
        return False
    try:
        config = parse_xray_subscription(content)
    except ValueError:
        return False
    return any(is_supported_proxy_outbound(outbound) for outbound in proxy_outbounds(config))


def _probe_observation(
    action: ResourceAction,
    *,
    passed: bool,
    remote_id: str,
) -> ResourceObservation:
    satisfied = (
        sorted(postcondition_key(item.kind, item.target_ref, item.detail) for item in action.resource.postconditions)
        if passed
        else []
    )
    return ResourceObservation(
        exists=passed,
        observed_hash=action.expected_hash if passed else "",
        remote_id=remote_id if passed else "",
        satisfied_postconditions=satisfied,
    )


def _control_payload(action: ResourceAction) -> ControlServicePayload:
    payload = action.resource.payload
    if not isinstance(payload, ControlServicePayload):
        raise ResourceReconcileError(f"Control service driver received {payload.kind}.")
    return payload


def _exception_has_timeout(error: Exception) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        detail = f"{current} {getattr(current, 'hint', '')}".casefold()
        if "timed out" in detail or "timeout" in detail:
            return True
        current = current.__cause__ or current.__context__
    return False
