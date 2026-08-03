"""Unit tests for the CloudProvider abstraction.

These are the ONLY tests that exercise the infra-providers module in CI.
The real-VM harness (tests/realvm/) uses a live HetznerProvider, but that
runs only manually on a developer machine — see tests/realvm/README.md.
"""

from __future__ import annotations

import pytest

from meridian.infra.providers import CloudProvider, ProviderError, VMInstance, VMSpec


class TestVMSpec:
    def test_requires_minimum_fields(self) -> None:
        spec = VMSpec(name="test", image="ubuntu-24.04", size="cx22", region="nbg1")
        assert spec.name == "test"
        assert spec.image == "ubuntu-24.04"
        assert spec.size == "cx22"
        assert spec.region == "nbg1"
        assert spec.ssh_key_ids == []
        assert spec.labels == {}
        assert spec.cloud_init is None
        assert spec.extras == {}

    def test_extras_is_separate_per_instance(self) -> None:
        """Dataclass default_factory must not share state between instances."""
        a = VMSpec(name="a", image="i", size="s", region="r")
        b = VMSpec(name="b", image="i", size="s", region="r")
        a.extras["firewall"] = "x"
        assert b.extras == {}


class TestProviderError:
    def test_carries_provider_name(self) -> None:
        e = ProviderError("nope", provider="hetzner")
        assert e.provider == "hetzner"
        assert "nope" in str(e)

    def test_preserves_cause(self) -> None:
        orig = RuntimeError("upstream blew up")
        e = ProviderError("wrapper", provider="hetzner", cause=orig)
        assert e.__cause__ is orig


class TestCloudProviderAbstract:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            CloudProvider()  # type: ignore[abstract]

    def test_subclass_must_implement_all_abstracts(self) -> None:
        class Partial(CloudProvider):
            name = "partial"

            def create_vm(self, spec: VMSpec) -> VMInstance:
                return VMInstance(
                    id="x",
                    name="x",
                    status="running",
                    public_ipv4="198.51.100.10",
                    public_ipv6=None,
                    region="r",
                    labels={},
                    provider=self.name,
                )

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]  # missing most abstracts

    def test_fully_implemented_subclass_can_instantiate(self) -> None:
        class Stub(CloudProvider):
            name = "stub"

            def create_vm(self, spec: VMSpec) -> VMInstance:
                return VMInstance(
                    id="1",
                    name=spec.name,
                    status="running",
                    public_ipv4="198.51.100.1",
                    public_ipv6=None,
                    region=spec.region,
                    labels=spec.labels,
                    provider=self.name,
                )

            def destroy_vm(self, vm_id: str) -> None:
                pass

            def get_vm(self, vm_id: str) -> VMInstance | None:
                return None

            def list_vms(self, labels: dict[str, str] | None = None) -> list[VMInstance]:
                return []

            def upload_ssh_key(self, *, name: str, public_key: str) -> str:
                return "key-1"

            def delete_ssh_key(self, key_id: str) -> None:
                pass

        p = Stub()
        spec = VMSpec(name="n", image="i", size="s", region="r", labels={"a": "b"})
        inst = p.create_vm(spec)
        assert inst.provider == "stub"
        assert inst.name == "n"
        assert inst.labels == {"a": "b"}
        assert p.estimate_cost(spec) is None  # default base impl


class TestHetznerProviderImportable:
    """The Hetzner impl must be importable (module-level import) even if
    `hcloud` SDK is not installed — the ImportError is deferred to
    __init__, not triggered on module load. This keeps `tests/realvm/`
    orchestrator safe to import in pytest-collected paths."""

    def test_module_imports_without_hcloud(self) -> None:
        # Smoke test — just importing the module must not raise.
        import meridian.infra.providers.hetzner as mod

        assert mod.HetznerProvider is not None

    def test_instantiation_without_hcloud_raises_provider_error(self, monkeypatch) -> None:
        """If hcloud is absent, HetznerProvider.__init__ must raise our
        typed ProviderError (not ImportError) so callers can handle it."""
        import sys

        # Pretend hcloud isn't installed by shadowing the import.
        monkeypatch.setitem(sys.modules, "hcloud", None)
        from meridian.infra.providers.hetzner import HetznerProvider

        with pytest.raises(ProviderError) as exc_info:
            HetznerProvider(token="x")
        assert "hcloud SDK not installed" in str(exc_info.value)
        assert exc_info.value.provider == "hetzner"

    def test_empty_token_rejected(self) -> None:
        from meridian.infra.providers.hetzner import HetznerProvider

        with pytest.raises(ProviderError) as exc_info:
            HetznerProvider(token="")
        assert "token is empty" in str(exc_info.value).lower()

    def test_estimate_cost_known_size(self) -> None:
        from meridian.infra.providers.hetzner import HetznerProvider

        # Stub that doesn't need a real token — we only call estimate_cost.
        # Bypass __init__ by constructing through object.__new__.
        p = object.__new__(HetznerProvider)
        # cx23 = new-gen replacement for deprecated cx22, same 2 vCPU / 4 GB
        spec = VMSpec(name="n", image="i", size="cx23", region="nbg1")
        cost = p.estimate_cost(spec, hours=1.0)
        assert cost is not None
        assert 0 < cost < 0.1  # sanity — cheapest tier

    def test_estimate_cost_unknown_size(self) -> None:
        from meridian.infra.providers.hetzner import HetznerProvider

        p = object.__new__(HetznerProvider)
        spec = VMSpec(name="n", image="i", size="unobtainium-99", region="nbg1")
        assert p.estimate_cost(spec, hours=1.0) is None
