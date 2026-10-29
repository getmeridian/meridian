"""The fleet document: secret-free export, round trip, refusals, and the intent diff."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from meridian.cluster import (
    ClusterConfig,
    ManagedResourceBinding,
    PanelConfig,
    RealityKeyBinding,
    ResourceAllocation,
    WorkloadBinding,
)
from meridian.core.fleet_document import FLEET_DOCUMENT_SCHEMA, IntentChange, change_line, intent_changes
from meridian.core.redaction import is_sensitive_key, redact_diagnostic_text
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    EgressOverrideIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.core.validation import CoreInputError
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.fleet_document import (
    dump_fleet_document,
    fleet_document,
    intent_from_document,
    parse_fleet_document,
)

SERVERS = {
    "srv-control": ("198.51.100.10", "helsinki"),
    "srv-exit-b": ("198.51.100.11", "frankfurt"),
    "srv-relay": ("198.51.100.12", ""),
    "srv-spare": ("198.51.100.13", "spare"),
}
XHTTP_TOKEN = "3f9a1c7e5b2d4a60"
PRIVATE_KEY = "kPq8Vx2mZr5Tn7Wb3Yc9Hd4Jf6Lg1Ns0QaRtUvWxYz0"
API_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtZXJpZGlhbiJ9.c2lnbmF0dXJlLXZhbHVl"


def _intent(**changes: Any) -> SetupIntent:
    data: dict[str, Any] = SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-control",
                paths=[
                    ProtocolPathIntent(id="exit-a-reality", protocol="reality", reality_sni="www.example.com"),
                    ProtocolPathIntent(
                        id="exit-a-xhttp",
                        protocol="xhttp",
                        tls_sni="198.51.100.10",
                        host="198.51.100.10",
                        path=XHTTP_TOKEN,
                    ),
                ],
            ),
            ExitIntent(
                id="exit-b",
                server_ref="srv-exit-b",
                paths=[ProtocolPathIntent(id="exit-b-reality", protocol="reality", reality_sni="www.example.org")],
                egress_overrides=[
                    EgressOverrideIntent(id="ai", match="geosite", values=["openai"], action="warp"),
                ],
            ),
        ],
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-b",
                hop_server_refs=["srv-relay"],
                exit_ref="exit-b",
                protocol_path_ref="exit-b-reality",
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["alice", "bob"], display_names={"bob": "Боб"}),
    ).model_dump(mode="json")
    data.update(changes)
    return SetupIntent.model_validate(data)


def _cluster(intent: SetupIntent | None = None) -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/panel", api_token=API_TOKEN, secret_path="a1b2c3d4e5f6"),
        topology_intent=intent or _intent(),
        workloads=[
            WorkloadBinding(
                id="exit-a",
                server_refs=["srv-control"],
                config_profile_uuid="6f1c1f3e-8f1d-4b51-9b1e-0d5f3b8f2a11",
                reality_keys={
                    "srv-control": RealityKeyBinding(
                        public_key="pub-key-value", private_key=PRIVATE_KEY, short_id="ab12"
                    )
                },
            )
        ],
        managed_bindings=[
            ManagedResourceBinding(
                logical_id="host:exit-a-reality",
                remote_id="0e6f3c1a-5d2b-4c8e-9f7a-1b2c3d4e5f60",
                active=True,
            )
        ],
        allocations={"exit-a-xhttp": ResourceAllocation(logical_id="exit-a-xhttp", path="alloc-secret-path")},
    )


@pytest.fixture
def registry(servers_file: Path) -> ServerRegistry:
    saved = ServerRegistry(servers_file)
    for server_id, (host, name) in SERVERS.items():
        saved.add(ServerEntry(host=host, name=name, id=server_id))
    return saved


def _document_text(registry: ServerRegistry, intent: SetupIntent | None = None) -> str:
    return dump_fleet_document(fleet_document(intent or _intent(), registry))


def _edited(registry: ServerRegistry, edit: Any) -> str:
    document = yaml.safe_load(_document_text(registry))
    edit(document)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


class TestExport:
    def test_names_servers_by_saved_name_or_address(self, registry: ServerRegistry) -> None:
        document = yaml.safe_load(_document_text(registry))

        assert document["schema"] == FLEET_DOCUMENT_SCHEMA
        assert document["control"]["server_ref"] == "helsinki"
        assert [exit_["server_ref"] for exit_ in document["exits"]] == ["helsinki", "frankfurt"]
        assert document["transparent_relays"][0]["hop_server_refs"] == ["198.51.100.12"]

    def test_document_holds_no_secret_shaped_value(self, registry: ServerRegistry) -> None:
        cluster = _cluster()
        text = _document_text(registry, cluster.topology_intent)

        assert redact_diagnostic_text(text, cluster.secret_values()) == text
        for secret in (XHTTP_TOKEN, PRIVATE_KEY, API_TOKEN, "pub-key-value", "alloc-secret-path"):
            assert secret not in text
        assert not re.search(r"\bsrv-[\w-]+", text)

        def keys(value: Any) -> list[str]:
            if isinstance(value, dict):
                return [*value, *(key for item in value.values() for key in keys(item))]
            if isinstance(value, list):
                return [key for item in value for key in keys(item)]
            return []

        assert not [key for key in keys(yaml.safe_load(text)) if is_sensitive_key(key) or key == "path"]


class TestRoundTrip:
    def test_unedited_file_reads_back_to_the_same_intent(self, registry: ServerRegistry) -> None:
        intent = _intent()
        restored = intent_from_document(parse_fleet_document(_document_text(registry, intent)), intent, registry)

        assert restored == intent
        assert intent_changes(intent, restored) == []


class TestDiff:
    def test_lines_read_like_the_review(self, registry: ServerRegistry) -> None:
        current = _intent()

        def edit(document: dict[str, Any]) -> None:
            document["access"]["users"].append("carol")
            document["exits"][1]["paths"][0]["reality_sni"] = "www.example.net"
            document["transparent_relays"] = []
            document["exits"][1]["egress_overrides"][0]["action"] = "direct"

        updated = intent_from_document(parse_fleet_document(_edited(registry, edit)), current, registry)

        assert [change_line(change) for change in intent_changes(current, updated)] == [
            "~ exit-b sni www.example.org -> www.example.net",
            "~ exit-b ai action warp -> direct",
            "- relay relay-b",
            "+ client carol",
        ]

    @pytest.mark.parametrize(
        ("edit", "expected"),
        [
            (lambda d: d.update(harden=False), "~ fleet harden true -> false"),
            (lambda d: d["access"]["display_names"].update(bob="Роберт"), "~ bob name Боб -> Роберт"),
            (lambda d: d["access"]["users"].remove("alice"), "- client alice"),
            (
                lambda d: d["exits"][0]["paths"][1].update(fingerprint="firefox"),
                "~ exit-a xhttp fingerprint chrome -> firefox",
            ),
            (lambda d: d["exits"][0]["paths"].pop(1), "- path exit-a xhttp"),
            (lambda d: d["exits"].reverse(), "~ exits order exit-a, exit-b -> exit-b, exit-a"),
            (lambda d: d["control"].update(title="Ours"), "~ control title Meridian -> Ours"),
        ],
        ids=["setting", "display-name", "client-removed", "path-field", "path-removed", "reorder", "control-title"],
    )
    def test_each_kind_of_edit_has_a_line(self, registry: ServerRegistry, edit: Any, expected: str) -> None:
        current = _intent()
        updated = intent_from_document(parse_fleet_document(_edited(registry, edit)), current, registry)

        assert [change_line(change) for change in intent_changes(current, updated)] == [expected]

    def test_new_xhttp_path_gets_a_fresh_transport_path(self, registry: ServerRegistry) -> None:
        current = _intent()

        def add_xhttp(document: dict[str, Any]) -> None:
            document["exits"][1]["paths"].append(
                {"id": "exit-b-xhttp", "protocol": "xhttp", "tls_sni": "198.51.100.11", "host": "198.51.100.11"}
            )

        updated = intent_from_document(parse_fleet_document(_edited(registry, add_xhttp)), current, registry)

        new = next(path for path in updated.exits[1].paths if path.protocol == "xhttp")
        assert re.fullmatch(r"[0-9a-f]{16}", new.path) and new.path != XHTTP_TOKEN
        assert intent_changes(current, updated) == [IntentChange(action="add", subject="path", target="exit-b xhttp")]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["exits"][1].update(server_ref="198.51.100.99"), "not saved: 198.51.100.99"),
        (lambda d: d["exits"][0]["paths"][1].update(path="/mine"), "transport paths"),
        (lambda d: d.update(schema="meridian.fleet/v9"), "Unsupported fleet file schema"),
        (lambda d: d.update(colour="blue"), "does not describe a valid fleet"),
        (lambda d: d["access"].update(users=[]), "does not describe a valid fleet"),
    ],
    ids=["unknown-server", "transport-path", "schema", "unknown-field", "no-clients"],
)
def test_document_refusals(registry: ServerRegistry, edit: Any, message: str) -> None:
    with pytest.raises(CoreInputError, match=message) as exc_info:
        intent_from_document(parse_fleet_document(_edited(registry, edit)), _intent(), registry)
    if "not saved" in message:
        assert "meridian server add" in exc_info.value.hint
