"""Node specs rendered from an exit's reviewed profile, and the local Reality key factory."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from meridian.compiler import compile_topology
from meridian.compiler.models import NodeWorkloadPayload
from meridian.config import XRAY_SHA256
from meridian.control.models import users_resource_id
from meridian.reconciler.agent_spec import CORE_API_PORT, build_node_spec, managed_inbounds
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.workloads import LocalRealityKeyFactory
from meridian.setup.single_server import single_server_intent


def _profile():
    plan = compile_topology(single_server_intent(None, server_ref="srv-a", address="198.51.100.10"))
    return next(item.payload for item in plan.resources if isinstance(item.payload, NodeWorkloadPayload)).profile


def _raw(key: str) -> bytes:
    return base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))


def test_managed_inbounds_carry_vision_only_on_reality() -> None:
    inbounds = managed_inbounds(_profile())
    flows = {inbound.tag.rsplit("-", 1)[-1]: (inbound.protocol, inbound.flow) for inbound in inbounds}

    assert flows == {
        "reality": ("vless", "xtls-rprx-vision"),
        "xhttp": ("vless", ""),
        "hysteria2": ("hysteria2", ""),
    }


def test_core_unit_restarts_on_users_and_every_pin() -> None:
    profile = _profile()
    spec = build_node_spec(profile, LocalRealityKeyFactory()("srv-a"), arch="x86_64")
    resources = {item.id: item for item in spec.resources}
    unit = resources["core-unit"]

    users = {users_resource_id(inbound.tag) for inbound in managed_inbounds(profile)}
    assert users <= set(unit.restart_on)  # type: ignore[union-attr]
    assert {"core-bin", "core-geoip", "core-geosite", "core-config"} <= set(unit.restart_on)  # type: ignore[union-attr]


def test_core_config_adds_loopback_api_first_in_routing() -> None:
    spec = build_node_spec(_profile(), LocalRealityKeyFactory()("srv-a"), arch="x86_64")
    config = next(item for item in spec.resources if item.id == "core-config").config  # type: ignore[union-attr]

    api = next(inbound for inbound in config["inbounds"] if inbound["tag"] == "api")
    assert (api["listen"], api["port"]) == ("127.0.0.1", CORE_API_PORT)
    assert config["routing"]["rules"][0] == {"type": "field", "inboundTag": ["api"], "outboundTag": "api"}


def test_local_reality_keys_are_a_matching_x25519_pair() -> None:
    keys = LocalRealityKeyFactory()("srv-a")
    private = X25519PrivateKey.from_private_bytes(_raw(keys.private_key))

    assert "=" not in keys.private_key + keys.public_key
    assert private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) == _raw(keys.public_key)
    assert len(bytes.fromhex(keys.short_id)) == 4


def test_pins_try_github_first_then_the_control_service_mirror() -> None:
    spec = build_node_spec(_profile(), LocalRealityKeyFactory()("srv-a"), arch="x86_64")
    pins = [item.sources for item in spec.resources if item.kind == "binary_pin"]  # type: ignore[union-attr]

    assert pins and all(sources[0].startswith("https://github.com/") for sources in pins)
    assert all(sources[-1] == "service" for sources in pins)


@pytest.mark.parametrize(
    ("arch", "asset", "size"),
    [("x86_64", "Xray-linux-64.zip", 21164807), ("aarch64", "Xray-linux-arm64-v8a.zip", 19699639)],
)
def test_each_architecture_pins_its_own_xray_archive(arch: str, asset: str, size: int) -> None:
    spec = build_node_spec(_profile(), LocalRealityKeyFactory()("srv-a"), arch=arch)

    pins = [resource for resource in spec.resources if resource.kind == "binary_pin"]
    assert len(pins) == 3 and {pin.sources[0].rsplit("/", 1)[1] for pin in pins} == {asset}
    assert {(pin.size, pin.sha256) for pin in pins} == {(size, XRAY_SHA256[asset])}


def test_an_architecture_without_a_pinned_xray_is_refused() -> None:
    with pytest.raises(ResourceReconcileError, match="no Linux build Meridian pins for riscv64"):
        build_node_spec(_profile(), LocalRealityKeyFactory()("srv-a"), arch="riscv64")
