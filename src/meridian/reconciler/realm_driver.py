"""SSH driver for transparent relay hops: one hashed systemd unit and config per hop."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from meridian.binary_pins import REALM_SHA256_PIN_PREFIX
from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import RealmHopPayload
from meridian.config import LEGACY_REALM_BINARY_PATH, REALM_SHA256, REALM_VERSION, RELAY_BINARY_PATH
from meridian.provision.ensure import ensure_file_content
from meridian.provision.relay import InstallRealm, RelayContext, parse_realm_version
from meridian.reconciler import server_retirement as retirement
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    payload_of,
)
from meridian.reconciler.server_evidence import (
    build_observation,
    content_mismatch,
    file_content_or_absent,
    port_is_listening,
    require_available_evidence,
    require_successful_evidence,
    service_is_active,
)
from meridian.reconciler.server_render import (
    legacy_realm_config_path,
    legacy_realm_service_name,
    realm_config_path,
    realm_service_name,
    render_realm_config,
    render_realm_unit,
    unit_path,
)
from meridian.reconciler.server_support import (
    raise_if_step_timeout,
    raise_if_timeout,
    restore_file,
    runtime_pin,
    runtime_pin_group,
)

if TYPE_CHECKING:
    from meridian.reconciler.server_drivers import ServerDriverContext
    from meridian.ssh import ServerConnection


class RealmHopDriver(retirement.RealmHopRetirement):
    """One relay hop: the forwarder binary plus a hashed config and systemd unit.

    Hops installed before neutral naming run as ``meridian-realm-*`` from
    ``/usr/local/bin/realm``. Observation reports them as drift; apply stops the
    legacy unit, starts the renamed one on the same port, then deletes the legacy
    files, restarting the legacy unit if the renamed one fails.
    """

    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        logical_id = action.resource.logical_id
        payload = payload_of(action, RealmHopPayload)
        conn = self.context.connection(payload.server_ref)
        service = realm_service_name(logical_id)
        expected_config = render_realm_config(payload, self.context.server_addresses)
        expected_unit = render_realm_unit(logical_id)
        config_content = file_content_or_absent(
            logical_id, conn.get_text(realm_config_path(logical_id), timeout=15), "Realm configuration"
        )
        unit_content = file_content_or_absent(
            logical_id, conn.get_text(unit_path(service), timeout=15), "Realm systemd unit"
        )
        legacy_unit = file_content_or_absent(
            logical_id,
            conn.get_text(unit_path(legacy_realm_service_name(logical_id)), timeout=15),
            "legacy Realm systemd unit",
        )
        active = conn.run(f"systemctl is-active {shlex.quote(service)}", timeout=15)
        listening = conn.run("ss -H -lnt 2>/dev/null", timeout=15)
        version = conn.run(f"{shlex.quote(RELAY_BINARY_PATH)} --version 2>/dev/null", timeout=15)
        expected_version = runtime_pin(self.context.plan, "realm_version", REALM_VERSION)
        service_active = service_is_active(logical_id, active, "Realm service")
        require_successful_evidence(logical_id, listening, "TCP listener inspection")
        # A binary that runs and fails (a missing glibc symbol) is broken, not unobservable: reinstall it.
        require_available_evidence(logical_id, version, "Realm version", missing_managed_binary=True)
        version_output = version.stdout if version.returncode == 0 else None
        exists = config_content is not None and unit_content is not None
        port_listening = port_is_listening(listening.stdout, payload.listen_port)
        version_ok = version_output is not None and parse_realm_version(version_output) == expected_version
        matches = (
            exists
            and legacy_unit is None
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
        mismatches = [
            reason
            for reason in (
                content_mismatch("the Realm configuration", config_content, expected_config),
                content_mismatch("the Realm unit", unit_content, expected_unit),
                "a pre-rename Realm unit remains" if legacy_unit is not None else "",
                "" if service_active else f"{service} is not active",
                "" if port_listening else f"nothing listens on port {payload.listen_port}",
                "" if version_ok else f"Realm is not the pinned {expected_version}",
            )
            if reason
        ]
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
                "legacy_unit": legacy_unit is not None,
            },
            satisfied=satisfied,
            mismatches=mismatches,
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        logical_id = action.resource.logical_id
        payload = payload_of(action, RealmHopPayload)
        conn = self.context.connection(payload.server_ref)
        relay_context = RelayContext(
            relay_ip=self.context.address(payload.server_ref),
            exit_ip=self.context.address(payload.target_server_ref),
            exit_port=payload.target_port,
            listen_port=payload.listen_port,
            realm_version=runtime_pin(self.context.plan, "realm_version", REALM_VERSION),
            realm_sha256=runtime_pin_group(self.context.plan, REALM_SHA256_PIN_PREFIX) or dict(REALM_SHA256),
        )
        installed = InstallRealm().run(conn, relay_context)
        if installed.status == "failed":
            raise_if_step_timeout(action, installed)
            raise ResourceReconcileError(f"Could not install the TCP forwarder: {installed.detail}")

        config_path = realm_config_path(logical_id)
        service = realm_service_name(logical_id)
        path = unit_path(service)
        legacy_service = legacy_realm_service_name(logical_id)
        has_legacy = conn.get_text(unit_path(legacy_service), timeout=15).returncode == 0
        previous_config = conn.get_text(config_path, timeout=15)
        previous_unit = conn.get_text(path, timeout=15)
        old_config = previous_config.stdout if previous_config.returncode == 0 else None
        old_unit = previous_unit.stdout if previous_unit.returncode == 0 else None
        config = ensure_file_content(
            conn,
            config_path,
            render_realm_config(payload, self.context.server_addresses),
            mode="600",
            create_parent=True,
            sensitive=True,
        )
        unit = ensure_file_content(conn, path, render_realm_unit(logical_id), mode="644", create_parent=True)
        if not config.ok or not unit.ok:
            raise_if_timeout(action, config.result)
            raise_if_timeout(action, unit.result)
            self._restore(conn, config_path, old_config, path, old_unit)
            raise ResourceReconcileError(f"Could not write forwarder service {service}.")
        conn.run("systemctl daemon-reload", timeout=30)
        if has_legacy:
            # Same listen port: the legacy unit must release it before the renamed one binds.
            conn.run(f"systemctl stop {shlex.quote(legacy_service)}", timeout=30)
        enabled = conn.run(f"systemctl enable {shlex.quote(service)}", timeout=15)
        restarted = conn.run(f"systemctl restart {shlex.quote(service)}", timeout=30)
        if enabled.returncode != 0 or restarted.returncode != 0:
            raise_if_timeout(action, enabled)
            raise_if_timeout(action, restarted)
            self._restore(conn, config_path, old_config, path, old_unit)
            conn.run("systemctl daemon-reload", timeout=30)
            if has_legacy:
                conn.run(f"systemctl disable --now {shlex.quote(service)} >/dev/null 2>&1", timeout=30)
                conn.run(f"systemctl start {shlex.quote(legacy_service)}", timeout=30)
            else:
                conn.run(f"systemctl restart {shlex.quote(service)}", timeout=30)
            raise ResourceReconcileError(f"Forwarder service {service} failed; previous configuration restored.")
        if has_legacy:
            self._remove_legacy(conn, logical_id)
        return ResourceApplyReceipt(remote_id=service)

    @staticmethod
    def _remove_legacy(conn: ServerConnection, logical_id: str) -> None:
        """Delete this hop's pre-rename unit and config; drop the old binary once no legacy unit uses it."""
        legacy_service = shlex.quote(legacy_realm_service_name(logical_id))
        legacy_unit = shlex.quote(unit_path(legacy_realm_service_name(logical_id)))
        result = conn.run(
            f"systemctl disable --now {legacy_service} >/dev/null 2>&1;"
            f" rm -f {legacy_unit} {shlex.quote(legacy_realm_config_path(logical_id))}"
            f" && systemctl daemon-reload && systemctl reset-failed {legacy_service} >/dev/null 2>&1;"
            " if ! ls /etc/systemd/system/meridian-realm-*.service >/dev/null 2>&1; then"
            f" rm -f {shlex.quote(LEGACY_REALM_BINARY_PATH)};"
            " rmdir /etc/meridian/realm /etc/meridian 2>/dev/null; fi;"
            f" ! systemctl is-active --quiet {legacy_service}",
            timeout=60,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"The renamed forwarder runs, but legacy unit {legacy_service} remains.")

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
