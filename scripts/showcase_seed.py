"""An invented fleet, seeded through the real service; never included in a distribution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest import mock

from meridian.control.models import (
    ClientEndpoint,
    NodeSpec,
    PrivacySettings,
    RevisionProposal,
    RevisionResult,
    ServiceSettings,
    TokenCreate,
    UserImport,
    UserImportBatch,
    UserUpdate,
)
from meridian.control.periods import period_start
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from meridian.core.agent_protocol import (
    HealthCheck,
    NodeHealth,
    NodeVersions,
    ObservedStateReport,
    StatsReport,
    UserTraffic,
)
from meridian.core.agent_types import NodeStateRef
from meridian.core.notices import Notice, NoticeWrite
from meridian.core.owner_contact import OwnerContact
from meridian.core.server_allowances import ServerAllowanceSpec, ServerMetadata
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent

FLEET_ID = "e4c42d12-819b-5220-89ee-bbcc700736aa"
ADMIN_TOKEN = "demo-only-admin-token-no-live-service"
PUBLIC_URL = "https://198.51.100.1/demo"
PAGE_PERSON = "Luma"
PEOPLE = (PAGE_PERSON, "Tavi", "Neri", "Vela", "Kavi", "Sela")
NODES = (("de", "DE", "198.51.100.10"), ("nl", "NL", "198.51.100.20"))
GIB = 1024**3
# Uneven daily weights, so the usage chart looks like real use rather than a flat bar.
DAILY = (3, 5, 2, 6, 4, 7, 3, 8, 5, 4, 9, 6, 5, 7)


def used(person: int, day: int) -> int:
    """Cumulative weight through *day*; each person's week is shifted, and counters only grow."""
    return (person + 1) * sum(DAILY[(person * 3 + past) % len(DAILY)] for past in range(day + 1))


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def seed_service(service: ControlService, now: datetime) -> None:
    """Use service operations for accounts, accounting, delivery, privacy, and lifecycle facts."""
    service.set_privacy(PrivacySettings(device_limits="full", usage_history="daily", history_days=30), author="demo")
    settings = ServiceSettings(
        profile_title="Meridian · Demo",
        server_name="Meridian",
        public_url=PUBLIC_URL,
        exit_countries=[country for _, country, _ in NODES],
        owner_contact=OwnerContact(
            text="Need a hand? Open the owner console to explore this demo.", link=PUBLIC_URL + "/console/"
        ),
        servers={
            "srv-" + node: ServerAllowanceSpec(
                node_id=node,
                metadata=ServerMetadata(
                    allowance_bytes=(1 + index) * 1024 * GIB,
                    provider="Example Hosting " + country,
                    pay_on=(now + timedelta(days=5 + index * 9)).date(),
                    pay_every="month",
                ),
            )
            for index, (node, country, _) in enumerate(NODES)
        },
    )
    # Count each server's allowance from the start of the month, as a fleet set up earlier would.
    with mock.patch("meridian.control.service_settings.utc_now", lambda: stamp(period_start("month", now))):
        service.put_settings(settings)
    service.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username=name,
                    uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, "meridian-showcase-" + name)),
                    sub_token="DemoPersonToken_" + name,
                    created_at=stamp(now - timedelta(days=28 + index * 2)),
                    expire_at=stamp(now + timedelta(days=4 if index == 2 else 30 + index * 7)) if index != 5 else None,
                    traffic_limit_bytes=(10 if index == 4 else 100) * GIB if index != 5 else 0,
                    traffic_limit_strategy="month",
                    traffic_reset_at=stamp(period_start("month", now)),
                    note="Private demo note: " + name + " received a connection link.",
                    tag=("FAMILY", "FAMILY", "TRIAL", "FRIENDS", "TRIAL", "FRIENDS")[index],
                )
                for index, name in enumerate(PEOPLE)
            ]
        )
    )
    for node, country, address in NODES:
        service.put_node_spec(node, NodeSpec(resources=[]))
        for index, (protocol, fingerprint, copy) in enumerate(
            (
                ("reality", "chrome", False),
                ("reality", "firefox", True),
                ("xhttp", "chrome", False),
                ("hysteria2", "", False),
            )
        ):
            service.put_endpoint(
                f"{node}:{protocol}:{fingerprint or 'udp'}",
                ClientEndpoint(
                    remark=f"{country} · {protocol.capitalize()}" + (" · Firefox" if copy else ""),
                    protocol=protocol,  # type: ignore[arg-type]
                    address=address,
                    port=443,
                    position=(0 if node == "de" else 10) + index,
                    fingerprint=fingerprint,
                    fingerprint_copy=copy,
                    reality_sni="cover.example",
                    reality_public_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                    reality_short_id="0123abcd",
                    tls_sni=address,
                    path="/demo-xhttp",
                    salamander_password="demo-only-salamander",
                ),
            )
    intent = SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-de"),
        exits=[
            ExitIntent(
                id=node,
                server_ref="srv-" + node,
                region=country,
                paths=[ProtocolPathIntent(id=node + "-reality", protocol="reality", reality_sni="cover.example")],
            )
            for node, country, _ in NODES
        ],
        default_egress_ref="de",
        access=AccessIntent(users=["Tavi", "Neri"]),
    )
    proposal = service.revisions.propose(
        RevisionProposal(intent=intent.model_dump(mode="json"), plan_hash="1" * 64, review_hash="2" * 64), caller=ROOT
    )
    service.revisions.approve(proposal.number, caller=ROOT)
    service.revisions.record_result(proposal.number, RevisionResult(status="applied"))
    for day in range(14):
        moment = now - timedelta(days=13 - day)
        for node_index, (node, _, _) in enumerate(NODES):
            service.record_stats(
                node,
                StatsReport(
                    counter_epoch=str(uuid.uuid5(uuid.NAMESPACE_URL, "meridian-demo-counters-" + node)),
                    seq=day + 1,
                    collected_at=stamp(moment),
                    users=[
                        UserTraffic(
                            user_id=user.user_id,
                            uplink_bytes=used(index, day) * GIB // 90,
                            downlink_bytes=used(index, day) * GIB // (15 + 5 * node_index),
                        )
                        for index, name in enumerate(PEOPLE[:-1])
                        if (user := service.get_user(name)) is not None
                    ],
                ),
                now=stamp(moment),
            )
    service.update_user("Vela", UserUpdate(status="disabled"))
    service.update_user(PAGE_PERSON, UserUpdate(device_limit=3))
    service.notices.update(
        NoticeWrite(
            notice=Notice(text="Welcome, {name}. These connections are examples; no server here is real."), confirm=True
        )
    )
    for index, (node, _, _) in enumerate(NODES):
        status = service.node_status(node)
        ref = NodeStateRef(
            fleet_epoch=status.fleet_epoch, revision=status.revision or 1, sha256=status.sha256 or "0" * 64
        )
        report = ObservedStateReport(
            fleet_id=FLEET_ID,
            node_id=node,
            applied=ref,
            target=ref,
            resources=[],
            observed_at=stamp(now),
            health=NodeHealth(
                status="healthy",
                checks=[HealthCheck(name="xray", status="pass", message="Running")],
                uptime_seconds=(12 + index * 5) * 86400,
                load1_milli=120 + index * 60,
                memory_available_bytes=(1250 + index * 800) * 1024**2,
                disk_free_bytes=(18 + index * 20) * GIB,
            ),
            versions=NodeVersions(
                agent="0.2.1+demo",
                xray="26.7.28",
                realm=None,
                nginx="1.26.3",
                os_id="debian",
                os_version="13",
                kernel="6.12.0",
            ),
        )
        service.report(node, report, report.model_dump(mode="json"))
    service.enforce()
    for name, scope in (("Read-only dashboard", "read"), ("People helper", "people"), ("Retired laptop", "propose")):
        issued = service.tokens.issue(TokenCreate(name=name, scope=scope, expires_in_days=90), issuer=ROOT)  # type: ignore[arg-type]
        if name == "Retired laptop":
            service.tokens.revoke(issued.id)
