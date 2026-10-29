"""`meridian migrate` keeps every link: the Meridian backend publishes what the panel's Hosts did.

Before is what Remnawave served: its Hosts in the reviewed Remnawave plan and, for the
single-server fleet, the Base64 answer the golden corpus recorded from a real panel.
After is the control service, fed by the Meridian backend's compiled endpoints (through
the real endpoint driver, with the workload's Reality keys from `cluster.yml`) and the
user imported with Remnawave's UUID and short UUID.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from meridian.cluster import ClusterConfig, PanelConfig, RealityKeyBinding, WorkloadBinding
from meridian.compiler import compile_topology
from meridian.compiler.models import ClientEndpointPayload, HostPayload, ResourcePlan
from meridian.control.client import ControlClient
from meridian.control.models import UserImport, UserImportBatch
from meridian.core.topology import SetupIntent, TransparentRelayIntent
from meridian.node_deploy import panel_base_url
from meridian.reconciler.meridian_drivers import ClientEndpointDriver, MeridianDriverContext
from meridian.reconciler.resources import build_resource_actions
from meridian.reconciler.workloads import WorkloadStateManager
from meridian.setup.single_server import single_server_intent
from meridian.xray_workload import salamander_password
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.systemlab.golden_corpus import decode_body, load_corpus, normalize_response

EXIT, RELAY = "198.51.100.12", "198.51.100.21"
UUID = "3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f"
SHORT_UUID = "Zq8xT3vNw2LmK7pR"
SECRET_PATH, SUB_PATH = "a1b2c3d4e5f6a7b8c9d0e1f2", "connect7f3a9b2c"
KEYS = RealityKeyBinding(
    public_key="zo060cy2M-x7cMF4FKXHbs0CloUFDTRHRboFhw5YfVk",
    private_key="private-key-for-tests-only-000000000000000",
    short_id="b2b2b2b2",
)


def _flipped(intent: SetupIntent) -> SetupIntent:
    return intent.model_copy(update={"control": intent.control.model_copy(update={"backend": "meridian"})})


def _relay_intent() -> SetupIntent:
    """The rehearsal shape: one server (control and exit) plus a relay into its Reality path, two clients."""
    intent = single_server_intent(None, server_ref="srv-a", address=EXIT)
    intent = intent.model_copy(update={"access": intent.access.model_copy(update={"users": ["alice", "bob"]})})
    reality = next(path for path in intent.exits[0].paths if path.protocol == "reality")
    relay = TransparentRelayIntent(
        id="relay-a", hop_server_refs=["srv-relay"], exit_ref=intent.exits[0].id, protocol_path_ref=reality.id
    )
    control = intent.control.model_copy(update={"backend": "remnawave"})
    return intent.model_copy(update={"transparent_relays": [relay], "control": control})


def _cluster(intent: SetupIntent) -> ClusterConfig:
    """A Remnawave fleet's saved state: the panel's address and paths, and the exit's Reality keys."""
    return ClusterConfig(
        panel=PanelConfig(
            url=panel_base_url(EXIT, "", SECRET_PATH), api_token="panel-token", server_ip=EXIT, sub_path=SUB_PATH
        ),
        topology_intent=intent,
        workloads=[WorkloadBinding(id=intent.exits[0].id, generation=1, reality_keys={"srv-a": KEYS})],
        active_generation=1,
    )


def _addresses() -> dict[str, str]:
    return {"srv-a": EXIT, "srv-relay": RELAY}


@pytest.fixture
def service_url(tmp_path: Path) -> Iterator[str]:
    with serve_control(tmp_path) as url:
        yield url


def _publish(plan: ResourcePlan, cluster: ClusterConfig, url: str) -> dict[str, str]:
    """Run the Meridian backend's endpoint driver against the service; return endpoint IDs by logical ID."""
    context = MeridianDriverContext(
        plan=plan,
        cluster=cluster,
        client=lambda: ControlClient(url, ADMIN_TOKEN),
        workloads=WorkloadStateManager(cluster, persist=lambda _state: None),
        connection_for=lambda _ref: pytest.fail("publishing endpoints needs no SSH"),
        server_addresses=_addresses(),
        agent_binary=lambda _arch: pytest.fail("publishing endpoints installs no agent"),
    )
    driver = ClientEndpointDriver(context)
    published = {}
    for action in build_resource_actions(plan, generation=2):
        if isinstance(action.resource.payload, ClientEndpointPayload):
            published[action.resource.logical_id] = driver.apply(action, None).remote_id
    return published


def _import(url: str, username: str) -> None:
    """Import one user with the credentials Remnawave gave them."""
    with ControlClient(url, ADMIN_TOKEN) as client:
        client.import_users(UserImportBatch(users=[UserImport(username=username, uuid=UUID, sub_token=SHORT_UUID)]))


def _panel_entries(plan: ResourcePlan) -> list[tuple[Any, ...]]:
    """What the panel published: every Host Base64 clients see, with the Reality identity it served."""
    entries = []
    for resource in plan.resources:
        host = resource.payload
        if not isinstance(host, HostPayload) or host.is_hidden or "XRAY_BASE64" in host.exclude_from_subscription_types:
            continue
        reality = host.protocol == "reality"
        entries.append(
            (
                host.remark,
                host.protocol,
                host.address or _addresses()[host.address_server_ref],
                host.public_port,
                host.sni,
                host.host,
                host.path,
                host.fingerprint,
                host.view_position,
                KEYS.public_key if reality else "",
                KEYS.short_id if reality else "",
                salamander_password(KEYS) if host.protocol == "hysteria2" else "",
            )
        )
    return sorted(entries, key=lambda entry: entry[8])


def _service_entries(url: str, endpoint_ids: list[str]) -> list[tuple[Any, ...]]:
    entries = []
    with ControlClient(url, ADMIN_TOKEN) as client:
        for endpoint_id in endpoint_ids:
            endpoint = client.get_endpoint(endpoint_id)
            assert endpoint is not None
            entries.append(
                (
                    endpoint.remark,
                    endpoint.protocol,
                    endpoint.address,
                    endpoint.port,
                    endpoint.reality_sni or endpoint.tls_sni,
                    endpoint.host,
                    endpoint.path,
                    endpoint.fingerprint,
                    endpoint.position,
                    endpoint.reality_public_key,
                    endpoint.reality_short_id,
                    endpoint.salamander_password,
                )
            )
    return sorted(entries, key=lambda entry: entry[8])


def test_the_meridian_backend_publishes_every_entry_the_panel_served(service_url: str) -> None:
    intent = _relay_intent()
    cluster = _cluster(intent)
    before = _panel_entries(compile_topology(intent))

    published = _publish(compile_topology(_flipped(intent)), cluster, service_url)

    assert _service_entries(service_url, list(published.values())) == before
    assert any(entry[2] == RELAY for entry in before), "the relay's entry is part of the comparison"


def test_a_migrated_user_gets_the_base64_answer_remnawave_recorded(service_url: str) -> None:
    """The corpus's single-server fleet: compiled, migrated, and served by the control service."""
    capture = load_corpus()[("single", "curl")]
    intent = single_server_intent(None, server_ref="srv-a", address=EXIT)
    xhttp = next(path for path in intent.exits[0].paths if path.protocol == "xhttp")
    cluster = _cluster(intent)

    _publish(compile_topology(_flipped(intent)), cluster, service_url)
    _import(service_url, "default")
    with ControlClient(service_url, ADMIN_TOKEN) as client:
        body = client.fetch_subscription(SHORT_UUID)

    known = [
        ("short-uuid", SHORT_UUID),
        ("vless-uuid", UUID),
        ("sub-path", SUB_PATH),
        ("path", xhttp.path.lstrip("/")),
        ("salamander", salamander_password(KEYS)),
        ("reality-pbk", KEYS.public_key),
        ("short-id", KEYS.short_id),
        ("host:exit-b", EXIT),
    ]
    encoding, text = decode_body("text/plain", body.encode("utf-8"))
    served = normalize_response(200, [], text, encoding=encoding, known=known)
    assert (served.encoding, served.body) == (capture.response.encoding, capture.response.body)


def test_the_subscription_url_keeps_its_shape_when_the_service_takes_the_panels_address() -> None:
    """The bootstrap saves the service URL without the panel's trailing slash; links read only host and path."""
    panel = PanelConfig(url=panel_base_url(EXIT, "", SECRET_PATH), sub_path=SUB_PATH)
    service = PanelConfig(url=panel_base_url(EXIT, "", SECRET_PATH).rstrip("/"), sub_path=SUB_PATH)

    assert service.subscription_url(SHORT_UUID) == panel.subscription_url(SHORT_UUID)
    assert service.subscription_url(SHORT_UUID) == f"https://{EXIT}/{SUB_PATH}/sub/{SHORT_UUID}"
