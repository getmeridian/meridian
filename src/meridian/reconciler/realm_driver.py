"""SSH driver for transparent relay hops: one hashed systemd unit and config per hop."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import RealmHopPayload
from meridian.config import REALM_VERSION
from meridian.provision.ensure import ensure_file_content
from meridian.provision.relay import InstallRealm, RelayContext, parse_realm_version
from meridian.reconciler import server_retirement as retirement
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
)
from meridian.reconciler.server_evidence import (
    build_observation,
    file_content_or_absent,
    managed_command_output,
    port_is_listening,
    require_successful_evidence,
    service_is_active,
)
from meridian.reconciler.server_render import (
    realm_config_path,
    realm_service_name,
    render_realm_config,
    render_realm_unit,
)
from meridian.reconciler.server_support import (
    payload_of,
    raise_if_step_timeout,
    raise_if_timeout,
    restore_file,
    runtime_pin,
)

if TYPE_CHECKING:
    from meridian.reconciler.server_drivers import ServerDriverContext
    from meridian.ssh import ServerConnection


class RealmHopDriver(retirement.RealmHopRetirement):
    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = payload_of(action, RealmHopPayload)
        conn = self.context.connection(payload.server_ref)
        config_path = realm_config_path(action.resource.logical_id)
        unit_path = f"/etc/systemd/system/{realm_service_name(action.resource.logical_id)}.service"
        expected_config = render_realm_config(payload, self.context.server_addresses)
        expected_unit = render_realm_unit(action.resource.logical_id)
        current_config = conn.get_text(config_path, timeout=15)
        current_unit = conn.get_text(unit_path, timeout=15)
        config_content = file_content_or_absent(action.resource.logical_id, current_config, "Realm configuration")
        unit_content = file_content_or_absent(action.resource.logical_id, current_unit, "Realm systemd unit")
        service = realm_service_name(action.resource.logical_id)
        active = conn.run(f"systemctl is-active {shlex.quote(service)}", timeout=15)
        listening = conn.run("ss -H -lnt 2>/dev/null", timeout=15)
        version = conn.run("realm --version 2>/dev/null", timeout=15)
        expected_version = runtime_pin(self.context.plan, "realm_version", REALM_VERSION)
        service_active = service_is_active(action.resource.logical_id, active, "Realm service")
        require_successful_evidence(action.resource.logical_id, listening, "TCP listener inspection")
        version_output = managed_command_output(action.resource.logical_id, version, "Realm version")
        exists = config_content is not None and unit_content is not None
        port_listening = port_is_listening(listening.stdout, payload.listen_port)
        version_ok = version_output is not None and parse_realm_version(version_output) == expected_version
        matches = (
            exists
            and config_content == expected_config
            and unit_content == expected_unit
            and service_active
            and port_listening
            and version_ok
        )
        satisfied: set[str] = set()
        if exists:
            satisfied.add("exists")
        if port_listening:
            satisfied.add("listening")
        return build_observation(
            action,
            remote_id=service,
            exists=exists,
            matches=matches,
            projection={
                "config": config_content or "",
                "unit": unit_content or "",
                "active": service_active,
                "listening": port_listening,
                "version": (version_output or "").strip(),
            },
            satisfied=satisfied,
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = payload_of(action, RealmHopPayload)
        conn = self.context.connection(payload.server_ref)
        relay_context = RelayContext(
            relay_ip=self.context.address(payload.server_ref),
            exit_ip=self.context.address(payload.target_server_ref),
            exit_port=payload.target_port,
            listen_port=payload.listen_port,
            realm_version=runtime_pin(self.context.plan, "realm_version", REALM_VERSION),
        )
        installed = InstallRealm().run(conn, relay_context)
        if installed.status == "failed":
            raise_if_step_timeout(action, installed)
            raise ResourceReconcileError(f"Could not install Realm: {installed.detail}")

        config_path = realm_config_path(action.resource.logical_id)
        service = realm_service_name(action.resource.logical_id)
        unit_path = f"/etc/systemd/system/{service}.service"
        previous_config = conn.get_text(config_path, timeout=15)
        previous_unit = conn.get_text(unit_path, timeout=15)
        config = ensure_file_content(
            conn,
            config_path,
            render_realm_config(payload, self.context.server_addresses),
            mode="600",
            create_parent=True,
            sensitive=True,
        )
        unit = ensure_file_content(
            conn,
            unit_path,
            render_realm_unit(action.resource.logical_id),
            mode="644",
            create_parent=True,
        )
        if not config.ok or not unit.ok:
            raise_if_timeout(action, config.result)
            raise_if_timeout(action, unit.result)
            self._restore(
                conn,
                config_path,
                previous_config.stdout if previous_config.returncode == 0 else None,
                unit_path,
                previous_unit.stdout if previous_unit.returncode == 0 else None,
            )
            raise ResourceReconcileError(f"Could not write Realm service {service}.")
        conn.run("systemctl daemon-reload", timeout=30)
        enabled = conn.run(f"systemctl enable {shlex.quote(service)}", timeout=15)
        restarted = conn.run(f"systemctl restart {shlex.quote(service)}", timeout=30)
        if enabled.returncode != 0 or restarted.returncode != 0:
            raise_if_timeout(action, enabled)
            raise_if_timeout(action, restarted)
            self._restore(
                conn,
                config_path,
                previous_config.stdout if previous_config.returncode == 0 else None,
                unit_path,
                previous_unit.stdout if previous_unit.returncode == 0 else None,
            )
            conn.run("systemctl daemon-reload", timeout=30)
            conn.run(f"systemctl restart {shlex.quote(service)}", timeout=30)
            raise ResourceReconcileError(f"Realm service {service} failed; previous configuration restored.")
        return ResourceApplyReceipt(remote_id=service)

    @staticmethod
    def _restore(
        conn: ServerConnection,
        config_path: str,
        previous_config: str | None,
        unit_path: str,
        previous_unit: str | None,
    ) -> None:
        restore_file(conn, config_path, previous_config)
        restore_file(conn, unit_path, previous_unit)
