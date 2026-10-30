"""Print individual credentials only in the client-show command that requested them."""

from meridian.console import err_console
from meridian.core.clients import ClientConnections
from meridian.i18n import t


def print_connections(view: ClientConnections) -> None:
    err_console.print()
    err_console.print(t("client.connections"), markup=False)
    for connection in view.connections:
        protocol = connection.protocol or t("client.connection_unavailable")
        err_console.print(f"  {connection.remark} · {protocol}", markup=False)
        if connection.available:
            err_console.print(f"  {connection.uri}", markup=False, overflow="fold")
    if view.formats:
        err_console.print()
        err_console.print(t("client.connection_formats"), markup=False)
        for format_url in view.formats:
            err_console.print(f"  {format_url.format}: {format_url.url}", markup=False, overflow="fold")
