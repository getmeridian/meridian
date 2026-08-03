"""Abstract CloudProvider interface.

All concrete providers (Hetzner today; Scaleway/DO/Vultr later) implement
this small surface. The harness and any future user-facing code depends only
on the abstract class — providers are interchangeable.

State model: NO state file. Resources are discovered by cloud-native labels:
every VM carries `meridian-fleet-id=<uuid>` and a lifecycle tag. A crashed
run leaves tagged resources; a subsequent `list_vms(labels=...)` finds them
for cleanup. This avoids the fragility of a locally-kept state file that
can get out of sync with reality.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VMSpec:
    """Request for a new VM. Provider-agnostic fields only.

    Provider-specific knobs (Hetzner placement group, DO backups, etc.) go in
    ``extras`` — each provider picks up the keys it understands and ignores
    the rest. This lets callers express portable intents with opt-in native
    features.
    """

    name: str
    image: str  # e.g. "ubuntu-24.04"
    size: str  # e.g. "cx22" (Hetzner), "s-1vcpu-2gb" (DigitalOcean)
    region: str  # e.g. "nbg1" (Hetzner Nuremberg)
    ssh_key_ids: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    cloud_init: str | None = None  # user-data for cloud-init (optional)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class VMInstance:
    """A provisioned VM. Fields providers MUST populate."""

    id: str  # provider-native VM id
    name: str
    status: str  # "running", "off", "initializing", etc. — provider-specific but normalized where possible
    public_ipv4: str | None
    public_ipv6: str | None
    region: str
    labels: dict[str, str]
    provider: str  # "hetzner", "scaleway", ...
    raw: Any = None  # the native SDK object, for provider-specific fallback


class ProviderError(Exception):
    """Raised when a provider operation fails for an actionable reason.

    Wrap SDK-specific exceptions into this so callers don't need to import
    hcloud / boto3 / etc. to handle errors generically.
    """

    def __init__(self, message: str, *, provider: str, cause: BaseException | None = None):
        super().__init__(message)
        self.provider = provider
        self.__cause__ = cause


class CloudProvider(ABC):
    """Abstract interface for VM lifecycle across clouds.

    Contract:
    - Operations are synchronous (sync SDKs only). Parallelism is the
      caller's concern (e.g. ThreadPoolExecutor around ``create_vm``).
    - Errors raise ``ProviderError`` with an actionable message.
    - Label-based discovery: ``list_vms(labels=...)`` returns all VMs in the
      account matching the label subset. This is the idempotency primitive —
      callers don't track state, they query.
    """

    name: str  # subclass sets to "hetzner", "scaleway", etc.

    @abstractmethod
    def create_vm(self, spec: VMSpec) -> VMInstance:
        """Provision a new VM. Returns once the VM has reached 'running'
        status and has a public IP assigned. May block up to ~3 minutes."""
        ...

    @abstractmethod
    def destroy_vm(self, vm_id: str) -> None:
        """Delete a VM. Idempotent — destroying an already-gone VM is OK.
        Also releases any attached resources that were created alongside
        (e.g. Hetzner Primary IPs — if the VM owned them exclusively)."""
        ...

    @abstractmethod
    def get_vm(self, vm_id: str) -> VMInstance | None:
        """Fetch a VM by id. Returns None if no such VM exists."""
        ...

    @abstractmethod
    def list_vms(self, labels: dict[str, str] | None = None) -> list[VMInstance]:
        """List VMs in the account, optionally filtered by label subset.
        Passing None returns all VMs. Pass ``{"meridian-realvm-test": "1"}``
        to find all harness-owned VMs, including orphans from crashed runs."""
        ...

    @abstractmethod
    def upload_ssh_key(self, *, name: str, public_key: str) -> str:
        """Upload an SSH public key and return its provider-side id.
        Idempotent on name — if a key with this name already exists with
        the same fingerprint, returns the existing id."""
        ...

    @abstractmethod
    def delete_ssh_key(self, key_id: str) -> None:
        """Remove an SSH key. Used for cleanup after a run."""
        ...

    def estimate_cost(self, spec: VMSpec, hours: float = 1.0) -> float | None:
        """Estimate cost in EUR for running the given spec for ``hours``.
        Returns None if the provider doesn't expose a pricing API.
        Default: None. Subclasses override with real numbers."""
        return None
