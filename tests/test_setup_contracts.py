"""Contracts for resumable V4 setup and finite topology intent."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.core.setup import (
    SetupAccessSelection,
    SetupDraft,
    SetupExitPaths,
    SetupExitRole,
    SetupPathSelection,
    SetupRelayRole,
    SetupRoleSelection,
    SetupRoutingSelection,
)
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    DeliveryIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    SetupIntent,
    TransparentRelayIntent,
)


def _reality(path_id: str = "reality-primary") -> ProtocolPathIntent:
    return ProtocolPathIntent(id=path_id, protocol="reality", reality_sni="www.microsoft.com")


def _xhttp() -> ProtocolPathIntent:
    return ProtocolPathIntent(
        id="xhttp-fallback",
        protocol="xhttp",
        listen_port=8443,
        tls_sni="vpn.example.com",
        host="vpn.example.com",
        path="xhttp",
    )


def _roles() -> SetupRoleSelection:
    return SetupRoleSelection(
        control=ControlPlaneIntent(server_ref="srv-control", public_hostname="panel.example.com"),
        exits=[SetupExitRole(id="exit-a", server_ref="srv-exit", region="de")],
        transparent_relays=[
            SetupRelayRole(
                id="relay-a",
                hop_server_refs=["srv-relay"],
                exit_ref="exit-a",
                protocol_path_ref="reality-primary",
                reality_sni="relay.example.com",
            )
        ],
    )


def _paths() -> SetupPathSelection:
    return SetupPathSelection(exits=[SetupExitPaths(exit_ref="exit-a", paths=[_reality(), _xhttp()])])


def _routing() -> SetupRoutingSelection:
    return SetupRoutingSelection(
        default_egress_ref="primary",
        egress_pools=[EgressPoolIntent(id="primary", exit_refs=["exit-a"])],
    )


def _access() -> SetupAccessSelection:
    return SetupAccessSelection(
        access=AccessIntent(users=["default"]),
        delivery=DeliveryIntent(profile_title="Family VPN"),
    )


def _draft_at_review() -> SetupDraft:
    draft = SetupDraft(server_refs=["srv-control", "srv-exit", "srv-relay"]).complete_current_stage()
    draft = draft.model_copy(update={"roles": _roles()}).complete_current_stage()
    draft = draft.model_copy(update={"paths": _paths()}).complete_current_stage()
    draft = draft.model_copy(update={"routing": _routing()}).complete_current_stage()
    return draft.model_copy(update={"access": _access()}).complete_current_stage()


class TestProtocolPathIntent:
    def test_reality_keeps_camouflage_separate_from_http_tls(self) -> None:
        with pytest.raises(ValidationError, match="separate from TLS Host"):
            ProtocolPathIntent(
                id="reality",
                protocol="reality",
                reality_sni="www.microsoft.com",
                tls_sni="vpn.example.com",
            )

    def test_http_transports_require_certificate_host_and_path(self) -> None:
        with pytest.raises(ValidationError, match="require certificate SNI, Host, and path"):
            ProtocolPathIntent(id="wss", protocol="wss", tls_sni="vpn.example.com")

    def test_exit_requires_reality_and_unique_protocols(self) -> None:
        with pytest.raises(ValidationError, match="must keep a Reality path"):
            ExitIntent(id="exit-a", server_ref="srv-exit", paths=[_xhttp()])


class TestOrderedRoutes:
    def test_match_values_are_canonicalized(self) -> None:
        country = OrderedRouteIntent(
            id="regional",
            priority=10,
            match="country",
            match_values=["de"],
            target_ref="exit-a",
        )
        network = OrderedRouteIntent(
            id="private",
            priority=20,
            match="ip",
            match_values=["198.51.100.20/24"],
            action="block",
        )

        assert country.match_values == ["DE"]
        assert network.match_values == ["198.51.100.0/24"]

    def test_block_route_rejects_egress_target(self) -> None:
        with pytest.raises(ValidationError, match="Blocked routes cannot have"):
            OrderedRouteIntent(id="blocked", priority=1, action="block", target_ref="exit-a")


class TestSetupIntent:
    def test_transparent_relay_rejects_udp_path(self) -> None:
        exit_ = ExitIntent(
            id="exit-a",
            server_ref="srv-exit",
            paths=[
                _reality(),
                ProtocolPathIntent(
                    id="hy2",
                    protocol="hysteria2",
                    tls_sni="vpn.example.com",
                ),
            ],
        )

        with pytest.raises(ValidationError, match="cannot relay Hysteria2"):
            SetupIntent(
                control=ControlPlaneIntent(server_ref="srv-control"),
                exits=[exit_],
                transparent_relays=[
                    TransparentRelayIntent(
                        id="relay-a",
                        hop_server_refs=["srv-relay"],
                        exit_ref="exit-a",
                        protocol_path_ref="hy2",
                    )
                ],
                default_egress_ref="exit-a",
                access=AccessIntent(users=["default"]),
            )

    def test_custom_reality_sni_rejects_non_reality_path(self) -> None:
        exit_ = ExitIntent(
            id="exit-a",
            server_ref="srv-exit",
            paths=[
                _reality(),
                _xhttp(),
            ],
        )

        with pytest.raises(ValidationError, match="only target a Reality path"):
            SetupIntent(
                control=ControlPlaneIntent(server_ref="srv-control"),
                exits=[exit_],
                transparent_relays=[
                    TransparentRelayIntent(
                        id="relay-a",
                        hop_server_refs=["srv-relay"],
                        exit_ref="exit-a",
                        protocol_path_ref="xhttp-fallback",
                        reality_sni="relay.example.com",
                    )
                ],
                default_egress_ref="exit-a",
                access=AccessIntent(users=["default"]),
            )

    def test_relay_chain_rejects_exit_server_as_a_hop(self) -> None:
        with pytest.raises(ValidationError, match="cannot include its exit server"):
            SetupIntent(
                control=ControlPlaneIntent(server_ref="srv-control"),
                exits=[
                    ExitIntent(
                        id="exit-a",
                        server_ref="srv-exit",
                        paths=[_reality()],
                    )
                ],
                transparent_relays=[
                    TransparentRelayIntent(
                        id="relay-a",
                        hop_server_refs=["srv-relay", "srv-exit"],
                        exit_ref="exit-a",
                        protocol_path_ref="reality-primary",
                    )
                ],
                default_egress_ref="exit-a",
                access=AccessIntent(users=["default"]),
            )

    def test_failover_pool_is_fail_closed(self) -> None:
        with pytest.raises(ValidationError, match="must fail closed"):
            EgressPoolIntent(id="primary", exit_refs=["exit-a"], fail_closed=False)

    def test_server_pool_rejects_fake_priority_failover(self) -> None:
        with pytest.raises(ValidationError, match="least_ping"):
            EgressPoolIntent(
                id="primary",
                exit_refs=["exit-a", "exit-b"],
                strategy="priority",  # type: ignore[arg-type]
            )


class TestSetupDraft:
    def test_progress_advances_in_order_and_builds_compiler_intent(self) -> None:
        draft = _draft_at_review()
        intent = draft.to_intent()

        assert draft.current_stage == "review"
        assert draft.completed_stages == ["servers", "roles", "paths", "routing", "access"]
        assert intent.control.server_ref == "srv-control"
        assert intent.exits[0].paths[1].protocol == "xhttp"
        assert intent.transparent_relays[0].hop_server_refs == ["srv-relay"]
        assert intent.transparent_relays[0].reality_sni == "relay.example.com"
        assert intent.default_egress_ref == "primary"

    def test_editing_paths_invalidates_every_downstream_stage(self) -> None:
        reviewed = _draft_at_review().model_copy(update={"review_hash": "sha256:review"}).complete_current_stage()

        invalidated = reviewed.invalidate_from("paths")

        assert invalidated.current_stage == "paths"
        assert invalidated.completed_stages == ["servers", "roles"]
        assert invalidated.roles is not None
        assert invalidated.paths is None
        assert invalidated.routing is None
        assert invalidated.access is None
        assert invalidated.review_hash == ""
        assert invalidated.applied_plan_hash == ""
        assert invalidated.verified_at == ""

    def test_role_servers_must_come_from_server_shelf_selection(self) -> None:
        draft = _draft_at_review().model_copy(update={"server_refs": ["srv-control", "srv-exit"]})

        with pytest.raises(ValueError, match="outside the saved selection"):
            draft.to_intent()

    def test_schema_cannot_store_setup_secrets(self) -> None:
        schema = SetupDraft.model_json_schema()
        property_names: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    property_names.update(str(name).lower() for name in properties)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(schema)

        assert not property_names & {
            "password",
            "api_token",
            "token",
            "private_key",
            "secret_key",
        }
