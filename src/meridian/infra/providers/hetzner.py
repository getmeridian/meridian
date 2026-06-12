"""Hetzner Cloud provider implementation.

Wraps the official ``hcloud`` Python SDK (maintained by Hetzner) behind the
abstract CloudProvider interface.

Install with: ``pip install meridian-vpn[hetzner]``

Pricing reference (as of early 2026; may drift — harness prints live numbers):
- CX22 (2 vCPU, 4 GB, 40 GB disk): ~€3.79/month ≈ €0.0053/hour
- CX32 (4 vCPU, 8 GB, 80 GB disk): ~€6.49/month ≈ €0.0091/hour
- Additional Primary IPv4:         ~€0.50/month ≈ €0.0007/hour
- Outbound traffic first 20 TB:    free
"""

from __future__ import annotations

import logging
from typing import Any

from meridian.infra.providers.base import CloudProvider, ProviderError, VMInstance, VMSpec

# Deferred hcloud imports — the SDK is optional. These are imported at class
# level only for type annotations in except clauses; actual constructors
# remain inside methods (see __init__ for the ImportError gate).
try:
    from hcloud import APIException as _APIException
    from hcloud.actions.domain import ActionException as _ActionException
except ImportError:  # SDK not installed — methods will never be called
    _APIException = Exception  # type: ignore[assignment,misc]
    _ActionException = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger("meridian.infra.hetzner")

# Hourly rates in EUR, used by estimate_cost(). Source: Hetzner Cloud pricing page.
# Kept intentionally conservative (rounded up). Harness prints the figure so
# the user sees what they're committing to — if reality differs, they'll know.
#
# Hetzner deprecated the CX2x generation in late 2025 in favor of CX2x3
# (x86, cheaper) and kept CPX2x (AMD, faster). See
# https://docs.hetzner.com/cloud/changelog for per-location deprecation.
_HOURLY_EUR = {
    # New-gen x86 (CX23 line, Sept 2025+) — cheapest, recommended for tests
    "cx23": 0.007,
    "cx33": 0.011,
    "cx43": 0.020,
    "cx53": 0.038,
    # AMD EPYC (CPX line, current) — faster for CPU-bound workloads
    "cpx11": 0.009,
    "cpx21": 0.016,
    "cpx22": 0.013,
    "cpx31": 0.028,
    "cpx41": 0.053,
    # ARM (CAX line) — cheap, good perf/EUR, but not every image has ARM build
    "cax11": 0.008,
    "cax21": 0.013,
    "cax31": 0.026,
    # Dedicated vCPU (CCX) — enterprise tier, overkill for tests
    "ccx13": 0.026,
    "ccx23": 0.051,
}


class HetznerProvider(CloudProvider):
    name = "hetzner"

    def __init__(self, token: str):
        # Validate token BEFORE attempting SDK import — an empty token is
        # always wrong regardless of whether hcloud is installed, and this
        # ordering keeps the error message accurate for the actual problem.
        if not token:
            raise ProviderError(
                "Hetzner token is empty. Export HCLOUD_TOKEN with a read-write project token.",
                provider=self.name,
            )
        try:
            from hcloud import Client  # type: ignore[import-not-found]
        except ImportError as e:
            raise ProviderError(
                "hcloud SDK not installed. Install with: pip install meridian-vpn[hetzner]",
                provider=self.name,
                cause=e,
            ) from e
        self._client = Client(token=token)

    # -------- VM lifecycle --------

    def create_vm(self, spec: VMSpec) -> VMInstance:
        from hcloud.images import Image
        from hcloud.locations import Location
        from hcloud.server_types import ServerType
        from hcloud.ssh_keys import SSHKey

        # Resolve string refs (image name, server type name, location name)
        # to SDK object references. The SDK accepts these "thin" objects.
        image = Image(name=spec.image)
        server_type = ServerType(name=spec.size)
        location = Location(name=spec.region)
        ssh_keys = [SSHKey(id=int(k)) for k in spec.ssh_key_ids]

        create_kwargs: dict[str, Any] = {
            "name": spec.name,
            "image": image,
            "server_type": server_type,
            "location": location,
            "ssh_keys": ssh_keys,
            "labels": spec.labels or None,
            "user_data": spec.cloud_init,
        }
        # Pass through any extras the caller knew to pass (placement_group,
        # networks, firewalls...). SDK will reject unknown kwargs — that's
        # the caller's problem to pass valid ones.
        for k, v in spec.extras.items():
            create_kwargs.setdefault(k, v)

        try:
            response = self._client.servers.create(**create_kwargs)
        except _APIException as e:
            raise ProviderError(
                f"Hetzner server create failed: {e}",
                provider=self.name,
                cause=e,
            ) from e

        server = response.server
        # Wait for the create action to finish (API call returns immediately).
        action = response.action
        try:
            action.wait_until_finished(max_retries=120)  # ~2 min cap
        except _ActionException as e:
            # Don't leave an orphan if create-action never resolves.
            try:
                server.delete()
            except _APIException as cleanup_err:  # Best-effort orphan cleanup
                logger.warning("Could not cleanup orphan server %s: %s", server.id, cleanup_err)
            raise ProviderError(
                f"Hetzner server create action did not finish: {e}",
                provider=self.name,
                cause=e,
            ) from e

        # Re-fetch to pick up the assigned public IP(s).
        assert server.id is not None
        refreshed = self._client.servers.get_by_id(server.id)
        return _to_vm_instance(refreshed)

    def destroy_vm(self, vm_id: str) -> None:
        try:
            server = self._client.servers.get_by_id(int(vm_id))
        except (ValueError, _APIException):
            # Already gone or invalid ID; idempotent success.
            return
        if server is None:
            return
        try:
            server.delete()
        except _APIException as e:
            raise ProviderError(
                f"Hetzner server delete failed: {e}",
                provider=self.name,
                cause=e,
            ) from e

    def get_vm(self, vm_id: str) -> VMInstance | None:
        try:
            server = self._client.servers.get_by_id(int(vm_id))
        except (ValueError, _APIException):
            return None
        if server is None:
            return None
        return _to_vm_instance(server)

    def list_vms(self, labels: dict[str, str] | None = None) -> list[VMInstance]:
        # hcloud accepts a label selector string (LHS=RHS,LHS2=RHS2).
        label_selector = None
        if labels:
            label_selector = ",".join(f"{k}={v}" for k, v in labels.items())
        try:
            servers = self._client.servers.get_all(label_selector=label_selector)
        except _APIException as e:
            raise ProviderError(
                f"Hetzner server list failed: {e}",
                provider=self.name,
                cause=e,
            ) from e
        return [_to_vm_instance(s) for s in servers]

    # -------- SSH keys --------

    def upload_ssh_key(self, *, name: str, public_key: str) -> str:
        # Hetzner reuses SSH keys across projects — a key with the same
        # fingerprint uploaded twice collides on the fingerprint, not the
        # name. Try to find an existing one by name first and reuse if the
        # public-key material matches.
        for existing in self._client.ssh_keys.get_all(name=name):
            if (existing.public_key or "").strip() == public_key.strip():
                return str(existing.id)
            # Name conflict with different material — Hetzner will reject
            # create; rename or delete first. We just raise a clear error.
            raise ProviderError(
                f"SSH key named {name!r} exists with different material. "
                f"Delete it via `hcloud ssh-key delete {name}` or choose a new name.",
                provider=self.name,
            )
        try:
            key = self._client.ssh_keys.create(name=name, public_key=public_key)
        except _APIException as e:
            raise ProviderError(
                f"Hetzner SSH key upload failed: {e}",
                provider=self.name,
                cause=e,
            ) from e
        return str(key.id)

    def delete_ssh_key(self, key_id: str) -> None:
        try:
            key = self._client.ssh_keys.get_by_id(int(key_id))
        except (ValueError, _APIException):
            return
        if key is None:
            return
        try:
            key.delete()
        except _APIException as e:
            raise ProviderError(
                f"Hetzner SSH key delete failed: {e}",
                provider=self.name,
                cause=e,
            ) from e

    # -------- Cost --------

    def estimate_cost(self, spec: VMSpec, hours: float = 1.0) -> float | None:
        hourly = _HOURLY_EUR.get(spec.size.lower())
        if hourly is None:
            return None
        return round(hourly * hours, 4)


def _to_vm_instance(server: Any) -> VMInstance:
    """Convert an hcloud Server object to our normalized VMInstance."""
    ipv4 = server.public_net.ipv4.ip if server.public_net and server.public_net.ipv4 else None
    # hcloud's ipv6 is a /64 subnet; the "first" address is the server's own.
    # Subnets look like '2a01:4f8:...:1234::/64' — strip the mask, append '::1' or
    # leave as-is if already specific. Hetzner assigns a routable /64; the
    # server's typical public v6 is <prefix>::1.
    ipv6_raw = server.public_net.ipv6.ip if server.public_net and server.public_net.ipv6 else None
    if ipv6_raw and "/" in ipv6_raw:
        ipv6 = ipv6_raw.split("/")[0].rstrip(":") + "::1"
    else:
        ipv6 = ipv6_raw
    return VMInstance(
        id=str(server.id),
        name=server.name,
        status=server.status,
        public_ipv4=ipv4,
        public_ipv6=ipv6,
        region=server.datacenter.location.name if server.datacenter else "",
        labels=dict(server.labels) if server.labels else {},
        provider="hetzner",
        raw=server,
    )
