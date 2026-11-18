"""Read saved profiles and their reviewed allowance metadata without contacting the fleet’s individual servers."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.markup import escape

from meridian import config, console
from meridian.commands._helpers import fail_meridian_v3_state, has_meridian_v3_state, load_cluster
from meridian.commands.server_display import print_metadata
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.server_allowances import ServerListEntry, ServerListResult
from meridian.core.server_calendar import next_payment_date
from meridian.i18n import t
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry


def run_list() -> None:
    operation = OperationContext()
    with console.error_context("server.list", timer=operation.timer):
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        try:
            entries = registry.list()
            cluster = load_cluster()
            intent = cluster.topology_intent
            refs = intent.declared_server_refs() if intent is not None else []
            by_profile = {}
            for ref in refs:
                profile = registry.find(ref)
                if profile is not None:
                    by_profile[profile.id] = ref
        except LocalStateError as exc:
            console.fail(exc)
        if not entries and has_meridian_v3_state():
            fail_meridian_v3_state()
        from meridian.commands.fleet_presentation import live_presentation, presentation_client
        from meridian.core.server_allowances import ServerMetadata

        metadata_by_ref: dict[str, ServerMetadata] = {}
        unavailable = False
        if refs and cluster.panel.url and cluster.panel.api_token:
            try:
                with presentation_client(cluster) as client:
                    metadata_by_ref = live_presentation(client).servers
            except MeridianError:
                # The SSH shelf is usable offline, but historic seeds are not current billing data.
                unavailable = True
        servers: list[ServerListEntry] = []
        today = datetime.now(UTC).date()
        for entry in entries:
            ref = by_profile.get(entry.id, entry.id)
            metadata = metadata_by_ref.get(ref)
            servers.append(
                ServerListEntry(
                    server_ref=ref,
                    name=entry.name,
                    host=entry.host,
                    ssh_user=entry.user,
                    ssh_port=entry.port,
                    declared=ref in refs,
                    metadata=metadata,
                    next_payment_on=next_payment_date(metadata.pay_on, metadata.pay_every, today) if metadata else None,
                )
            )
        result = ServerListResult(servers=servers)
        if console.is_json_mode():
            emit_json(
                command_envelope(
                    command="server.list",
                    data=result.to_data(),
                    warnings=[MeridianError("Current server billing settings are unavailable.").to_error_model()]
                    if unavailable
                    else [],
                    summary=Summary(text="Read saved servers and reviewed metadata", counts={"servers": len(servers)}),
                    timer=operation.timer,
                )
            )
            return
        if unavailable:
            console.warn(t("server_metadata.unavailable"))
        if not servers:
            console.info(t("server_metadata.empty"))
            return
        console.err_console.print()
        for server in servers:
            console.err_console.print(
                f"  [bold]{escape(server.name or server.server_ref)}[/bold]  "
                f"{escape(server.host)}  {escape(server.ssh_user)}:{server.ssh_port}"
            )
            if server.metadata is not None:
                print_metadata(server.metadata)
                if intent is not None and server.server_ref not in (
                    {item.server_ref for item in intent.exits} | {item.server_ref for item in intent.routing_gateways}
                ):
                    console.info(t("server_metadata.not_measured"))
                if server.next_payment_on is not None:
                    console.info(t("server_metadata.next_payment", date=str(server.next_payment_on)))
        console.err_console.print()
