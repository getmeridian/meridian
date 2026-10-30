"""Read or forget subscription devices through the control access adapter."""

from __future__ import annotations

from pydantic import ValidationError
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import load_topology_cluster
from meridian.commands.client_handoff import get_user_or_fail, resolve_client_mutation_name, resolve_client_name
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.control_access import access_panel
from meridian.core.client_devices import ClientDevice, ClientDevicesResult
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def run_devices(name: str, *, remove: str | None = None, clear: bool = False) -> None:
    operation = OperationContext()
    with error_context("client.devices", timer=operation.timer):
        if remove is not None and clear:
            fail("Choose --remove or --clear", hint="Remove one device ID, or clear them all.", hint_type="user")
        if remove is not None:
            try:
                ClientDevice(device_key=remove)
            except ValidationError:
                fail(
                    "Invalid device ID", hint="Copy the ID from meridian client devices NAME --json.", hint_type="user"
                )
        cluster = load_topology_cluster()
        mutating = remove is not None or clear
        if mutating:
            resolve_client_name(cluster, name, console_people=True)
        try:
            with access_panel(cluster) as panel:
                if mutating:
                    name = resolve_client_mutation_name(cluster, panel, name)
                else:
                    name = resolve_client_name(
                        cluster, name, console_people=True, console_names=[user.username for user in panel.list_users()]
                    )
                user = get_user_or_fail(panel, name, action="load")
                if user is None:
                    fail(f"Client '{name}' not found", hint="Check names with meridian client list.", hint_type="user")
                removed = panel.delete_user_devices(user.id, remove).deleted if mutating else 0
                view = panel.user_devices(user.id)
                result = ClientDevicesResult.model_validate({**view.to_data(), "removed": removed})
        except RemnawaveError as exc:
            fail(
                f"Could not manage devices for '{name}': {exc}",
                hint=exc.hint or "Check control service connectivity.",
                hint_type=exc.category,
            )
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="client.devices",
                    data=result.to_data(),
                    summary=Summary(
                        text=f"Devices for {name}",
                        changed=removed > 0,
                        counts={"devices": result.count, "removed": removed},
                    ),
                    timer=operation.timer,
                )
            )
            return
        err_console.print()
        err_console.print("  " + escape(t("client.devices_count", count=result.count, limit=result.limit)))
        if removed:
            err_console.print("  " + escape(t("client.devices_removed", count=removed)))
        if result.level == "off":
            err_console.print("  " + escape(t("client.devices_off")))
        elif result.level == "private":
            err_console.print("  " + escape(t("client.devices_private")))
        else:
            table = Table()
            for key in (
                "client.device_id",
                "client.device_system",
                "client.device_model",
                "client.device_app",
                "client.device_first_seen",
                "client.device_last_seen",
            ):
                table.add_column(t(key))
            for device in result.devices:
                details = device.to_data()
                details["platform"] = " ".join(
                    str(details.get(key) or "") for key in ("platform", "os_version")
                ).strip()
                table.add_row(
                    *(
                        escape(str(details.get(key) or "—"))
                        for key in ("device_key", "platform", "model", "user_agent", "first_seen_at", "last_seen_at")
                    )
                )
            err_console.print(table)
        err_console.print()
