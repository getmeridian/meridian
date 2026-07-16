"""SSH-backed drivers for reviewed V4 server resources."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    CertificatePayload,
    FirewallRulePayload,
    NginxArtifactPayload,
    NodeRuntimePayload,
    RealmHopPayload,
    ResourcePlan,
    ServerBaselinePayload,
    canonical_hash,
)
from meridian.config import REALM_VERSION, REMNAWAVE_NODE_DIR, REMNAWAVE_NODE_IMAGE
from meridian.node_deploy import deploy_node_container, render_node_compose
from meridian.provision.baseline import build_server_baseline_checks, build_server_baseline_steps
from meridian.provision.ensure import ensure_file_content, ensure_ufw_rule
from meridian.provision.nginx import InstallNginx
from meridian.provision.relay import InstallRealm, RelayContext
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.provision.tls import IssueTLSCert
from meridian.provision.warp import InstallWarp
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceDrivers,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.reconciler.server_render import (
    artifact_token,
    certificate_directory,
    nginx_artifact_path,
    realm_config_path,
    realm_service_name,
    render_nginx_artifact,
    render_realm_config,
    render_realm_unit,
)
from meridian.remnawave import MeridianPanel
from meridian.ssh import ServerConnection

ConnectionFactory = Callable[[str], ServerConnection]
PayloadT = TypeVar(
    "PayloadT",
    FirewallRulePayload,
    CertificatePayload,
    NginxArtifactPayload,
    RealmHopPayload,
    NodeRuntimePayload,
    ServerBaselinePayload,
)


@dataclass
class ServerDriverContext:
    plan: ResourcePlan
    cluster: ClusterConfig
    panel: MeridianPanel
    connection_for: ConnectionFactory
    server_addresses: Mapping[str, str]
    node_secrets: dict[str, str] = field(default_factory=dict, repr=False)

    def connection(self, server_ref: str) -> ServerConnection:
        return self.connection_for(server_ref)

    def address(self, server_ref: str) -> str:
        address = self.server_addresses.get(server_ref, "")
        if not address:
            raise ResourceReconcileError(
                f"Server {server_ref} has no resolved address.",
                hint="Validate the saved server before applying server resources.",
                category="user",
            )
        return address

    def remote_id(self, logical_id: str, generation: int) -> str:
        binding = self.cluster.managed_bindings.get(f"{logical_id}@{generation}")
        return binding.remote_id if binding is not None else ""


def build_server_drivers(context: ServerDriverContext) -> ResourceDrivers:
    """Build SSH drivers shared by exits and transparent relay chains."""
    return {
        "server_baseline": ServerBaselineDriver(context),
        "firewall_rule": FirewallRuleDriver(context),
        "certificate": CertificateDriver(context),
        "nginx_artifact": NginxArtifactDriver(context),
        "realm_hop": RealmHopDriver(context),
        "node_runtime": NodeRuntimeDriver(context),
    }


class ServerBaselineDriver:
    """Prepare packages, hardening, firewall, and optional Docker once per host."""

    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, ServerBaselinePayload)
        conn = self.context.connection(payload.server_ref)
        context = _baseline_context(payload, conn, self.context.address(payload.server_ref))
        checks = build_server_baseline_checks(
            context,
            install_docker=payload.install_docker,
            manage_public_ports=False,
        )
        outcomes = {check.name: conn.run(check.command, timeout=15).returncode == 0 for check in checks}
        marker = conn.get_text(_baseline_marker_path(action.resource.logical_id), timeout=15)
        attestation = _resource_contract_attestation(action, self.context.plan)
        marker_ok = marker.returncode == 0 and marker.stdout == f"{attestation}\n"
        matches = marker_ok and all(outcomes.values())
        return _observed(
            action,
            remote_id=payload.server_ref,
            exists=marker.returncode == 0,
            matches=matches,
            projection={
                "attestation": marker.stdout if marker.returncode == 0 else "",
                "checks": outcomes,
            },
            satisfied={"exists"} if matches else set(),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        from meridian.provision import Provisioner

        payload = _payload(action, ServerBaselinePayload)
        conn = self.context.connection(payload.server_ref)
        context = _baseline_context(payload, conn, self.context.address(payload.server_ref))
        results = Provisioner(
            build_server_baseline_steps(
                context,
                install_docker=payload.install_docker,
                manage_public_ports=False,
            )
        ).run(conn, context)
        failures = [result for result in results if result.status == "failed"]
        if failures:
            failure = failures[0]
            _raise_if_step_timeout(action, failure)
            raise ResourceReconcileError(
                f"Could not prepare server {payload.server_ref}: {failure.name}: {failure.detail}"
            )
        attestation = ensure_file_content(
            conn,
            _baseline_marker_path(action.resource.logical_id),
            f"{_resource_contract_attestation(action, self.context.plan)}\n",
            mode="600",
            create_parent=True,
        )
        if not attestation.ok:
            _raise_if_timeout(action, attestation.result)
            raise ResourceReconcileError(f"Could not attest server baseline {payload.server_ref}: {attestation.detail}")
        return ResourceApplyReceipt(remote_id=payload.server_ref)


class FirewallRuleDriver:
    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, FirewallRulePayload)
        conn = self.context.connection(payload.server_ref)
        result = conn.run("ufw show added 2>/dev/null", timeout=15)
        rules = _firewall_rules(action.resource.logical_id, payload, self.context.server_addresses)
        matches = result.returncode == 0 and all(rule in result.stdout for rule in rules)
        return _observed(
            action,
            remote_id=_firewall_marker(action.resource.logical_id),
            exists=matches,
            matches=matches,
            projection={"rules": result.stdout.splitlines() if result.returncode == 0 else []},
            satisfied={"exists"} if matches else set(),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, FirewallRulePayload)
        conn = self.context.connection(payload.server_ref)
        for rule in _firewall_rules(action.resource.logical_id, payload, self.context.server_addresses):
            result = ensure_ufw_rule(conn, rule)
            if not result.ok:
                _raise_if_timeout(action, result.result)
                raise ResourceReconcileError(
                    f"Could not apply firewall rule {action.resource.logical_id}: {result.detail}"
                )
        return ResourceApplyReceipt(remote_id=_firewall_marker(action.resource.logical_id))


class CertificateDriver:
    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, CertificatePayload)
        conn = self.context.connection(payload.server_ref)
        cert_dir = certificate_directory(payload.hostname)
        certificate_path = f"{cert_dir}/fullchain.pem"
        result = conn.run(
            f"openssl x509 -in {shlex.quote(certificate_path)} -noout -checkend 86400 -ext subjectAltName 2>/dev/null",
            timeout=15,
        )
        matches = result.returncode == 0 and f"DNS:{payload.hostname}" in result.stdout
        return _observed(
            action,
            remote_id=certificate_path,
            exists=result.returncode == 0,
            matches=matches,
            projection={"certificate": result.stdout},
            satisfied={"exists"} if result.returncode == 0 else set(),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, CertificatePayload)
        conn = self.context.connection(payload.server_ref)
        installed = InstallNginx().run(
            conn,
            ProvisionContext(
                ip=self.context.address(payload.server_ref),
                domain=payload.hostname,
                is_panel_host=False,
            ),
        )
        if installed.status == "failed":
            raise ResourceReconcileError(f"Could not install nginx: {installed.detail}")
        challenge_path = "/etc/nginx/sites-available/meridian-v4-acme.conf"
        challenge = (
            "# Managed by Meridian v4 for ACME HTTP-01.\n"
            "server {\n"
            "    listen 80 default_server;\n"
            "    listen [::]:80 default_server;\n"
            "    server_name _;\n"
            "    location /.well-known/acme-challenge/ { root /var/www/acme; }\n"
            "    location / { return 404; }\n"
            "}\n"
        )
        written = ensure_file_content(
            conn,
            challenge_path,
            challenge,
            mode="644",
            create_parent=True,
        )
        if not written.ok:
            _raise_if_timeout(action, written.result)
            raise ResourceReconcileError(f"Could not configure ACME challenge listener: {written.detail}")
        enabled_path = "/etc/nginx/sites-enabled/meridian-v4-acme.conf"
        linked = conn.run(
            f"rm -f /etc/nginx/sites-enabled/default && "
            f"ln -sfn {shlex.quote(challenge_path)} {shlex.quote(enabled_path)}",
            timeout=15,
        )
        validation = conn.run("nginx -t 2>&1", timeout=15)
        if linked.returncode != 0 or validation.returncode != 0:
            _raise_if_timeout(action, linked)
            raise ResourceReconcileError("Could not start the ACME HTTP-01 challenge listener.")
        reload_result = conn.run("systemctl reload nginx", timeout=15)
        if reload_result.returncode != 0:
            _raise_if_timeout(action, reload_result)
            raise ResourceReconcileError("Could not start the ACME HTTP-01 challenge listener.")
        cert_dir = certificate_directory(payload.hostname)
        result = IssueTLSCert(
            payload.hostname,
            certificate_directory=cert_dir,
        ).run(
            conn,
            ProvisionContext(
                ip=self.context.address(payload.server_ref),
                domain=payload.hostname,
                is_panel_host=False,
            ),
        )
        if result.status == "failed":
            raise ResourceReconcileError(f"Could not issue TLS certificate for {payload.hostname}: {result.detail}")
        return ResourceApplyReceipt(remote_id=f"{cert_dir}/fullchain.pem")


class NginxArtifactDriver:
    _LEGACY_STREAM_PATH = "/etc/nginx/stream.d/meridian.conf"

    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, NginxArtifactPayload)
        conn = self.context.connection(payload.server_ref)
        path = nginx_artifact_path(action.resource.logical_id, payload.layer)
        expected = render_nginx_artifact(
            action.resource.logical_id,
            payload,
            self.context.server_addresses,
        )
        current = conn.get_text(path, timeout=15)
        legacy_absent = True
        if payload.layer == "stream" and payload.listener_port == 443:
            legacy = conn.run(
                f"test ! -e {shlex.quote(self._LEGACY_STREAM_PATH)}",
                timeout=15,
            )
            legacy_absent = legacy.returncode == 0
        active = conn.run("systemctl is-active nginx", timeout=15)
        listening = conn.run("ss -H -lnt 2>/dev/null", timeout=15)
        file_matches = current.returncode == 0 and current.stdout == expected
        service_active = active.returncode == 0 and active.stdout.strip() == "active"
        port_listening = _port_in_ss(listening.stdout, payload.listener_port)
        matches = file_matches and service_active and port_listening and legacy_absent
        satisfied: set[str] = set()
        if current.returncode == 0:
            satisfied.add("exists")
        if port_listening:
            satisfied.add("listening")
        return _observed(
            action,
            remote_id=path,
            exists=current.returncode == 0,
            matches=matches,
            projection={
                "content": current.stdout if current.returncode == 0 else "",
                "active": service_active,
                "listening": port_listening,
                "legacy_absent": legacy_absent,
            },
            satisfied=satisfied,
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, NginxArtifactPayload)
        conn = self.context.connection(payload.server_ref)
        address = self.context.address(payload.server_ref)
        installed = InstallNginx().run(
            conn,
            ProvisionContext(ip=address, is_panel_host=False),
        )
        if installed.status == "failed":
            raise ResourceReconcileError(f"Could not install nginx: {installed.detail}")
        self._ensure_stream_include(conn)

        path = nginx_artifact_path(action.resource.logical_id, payload.layer)
        expected = render_nginx_artifact(
            action.resource.logical_id,
            payload,
            self.context.server_addresses,
        )
        previous = conn.get_text(path, timeout=15)
        previous_content = previous.stdout if previous.returncode == 0 else None
        owns_legacy_stream = payload.layer == "stream" and payload.listener_port == 443
        legacy_content: str | None = None
        if owns_legacy_stream:
            legacy = conn.get_text(self._LEGACY_STREAM_PATH, timeout=15)
            legacy_content = legacy.stdout if legacy.returncode == 0 else None
        enabled = ""
        written = ensure_file_content(
            conn,
            path,
            expected,
            mode="644",
            create_parent=True,
        )
        if not written.ok:
            _raise_if_timeout(action, written.result)
            raise ResourceReconcileError(f"Could not write nginx artifact {path}: {written.detail}")
        if owns_legacy_stream:
            removed = conn.run(
                f"rm -f {shlex.quote(self._LEGACY_STREAM_PATH)}",
                timeout=15,
            )
            if removed.returncode != 0:
                _raise_if_timeout(action, removed)
                self._restore_artifact(conn, path, previous_content, enabled)
                self._restore_file(conn, self._LEGACY_STREAM_PATH, legacy_content)
                raise ResourceReconcileError("Could not retire the legacy nginx stream artifact.")
        if payload.layer == "http":
            enabled = f"/etc/nginx/sites-enabled/meridian-v4-{artifact_token(action.resource.logical_id)}.conf"
            linked = conn.run(
                f"ln -sfn {shlex.quote(path)} {shlex.quote(enabled)}",
                timeout=15,
            )
            if linked.returncode != 0:
                _raise_if_timeout(action, linked)
                self._restore_artifact(conn, path, previous_content, enabled)
                raise ResourceReconcileError(f"Could not enable nginx artifact {path}.")
        validation = conn.run("nginx -t 2>&1", timeout=15)
        if validation.returncode != 0:
            self._restore_artifact(conn, path, previous_content, enabled)
            if owns_legacy_stream:
                self._restore_file(conn, self._LEGACY_STREAM_PATH, legacy_content)
            raise ResourceReconcileError(
                f"nginx rejected {path}: {(validation.stderr or validation.stdout).strip()[:200]}"
            )
        reload_result = conn.run("systemctl reload nginx", timeout=15)
        if reload_result.returncode != 0:
            _raise_if_timeout(action, reload_result)
            self._restore_artifact(conn, path, previous_content, enabled)
            if owns_legacy_stream:
                self._restore_file(conn, self._LEGACY_STREAM_PATH, legacy_content)
            conn.run("systemctl reload nginx", timeout=15)
            raise ResourceReconcileError(f"nginx reload failed for {path}.")
        return ResourceApplyReceipt(remote_id=path)

    @staticmethod
    def _ensure_stream_include(conn: ServerConnection) -> None:
        current = conn.get_text("/etc/nginx/nginx.conf", timeout=15)
        if current.returncode != 0:
            raise ResourceReconcileError("Could not read /etc/nginx/nginx.conf.")
        if "include /etc/nginx/stream.d/*.conf;" in current.stdout:
            return
        updated = current.stdout.rstrip() + "\n\nstream {\n    include /etc/nginx/stream.d/*.conf;\n}\n"
        result = ensure_file_content(conn, "/etc/nginx/nginx.conf", updated, mode="644")
        if not result.ok:
            raise ResourceReconcileError(f"Could not enable nginx stream includes: {result.detail}")

    @staticmethod
    def _restore_file(
        conn: ServerConnection,
        path: str,
        previous_content: str | None,
    ) -> None:
        if previous_content is None:
            conn.run(f"rm -f {shlex.quote(path)}", timeout=15)
            return
        ensure_file_content(conn, path, previous_content, mode="644", create_parent=True)

    @staticmethod
    def _restore_artifact(
        conn: ServerConnection,
        path: str,
        previous_content: str | None,
        enabled_path: str,
    ) -> None:
        NginxArtifactDriver._restore_file(conn, path, previous_content)
        if enabled_path and previous_content is None:
            conn.run(f"rm -f {shlex.quote(enabled_path)}", timeout=15)


class RealmHopDriver:
    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, RealmHopPayload)
        conn = self.context.connection(payload.server_ref)
        config_path = realm_config_path(action.resource.logical_id)
        unit_path = f"/etc/systemd/system/{realm_service_name(action.resource.logical_id)}.service"
        expected_config = render_realm_config(payload, self.context.server_addresses)
        expected_unit = render_realm_unit(action.resource.logical_id)
        current_config = conn.get_text(config_path, timeout=15)
        current_unit = conn.get_text(unit_path, timeout=15)
        service = realm_service_name(action.resource.logical_id)
        active = conn.run(f"systemctl is-active {shlex.quote(service)}", timeout=15)
        listening = conn.run("ss -H -lnt 2>/dev/null", timeout=15)
        version = conn.run("realm --version 2>/dev/null", timeout=15)
        expected_version = _runtime_pin(self.context.plan, "realm_version", REALM_VERSION)
        exists = current_config.returncode == 0 and current_unit.returncode == 0
        port_listening = _port_in_ss(listening.stdout, payload.listen_port)
        version_ok = version.returncode == 0 and version.stdout.strip().split()[-1:] == [expected_version]
        matches = (
            exists
            and current_config.stdout == expected_config
            and current_unit.stdout == expected_unit
            and active.returncode == 0
            and active.stdout.strip() == "active"
            and port_listening
            and version_ok
        )
        satisfied: set[str] = set()
        if exists:
            satisfied.add("exists")
        if port_listening:
            satisfied.add("listening")
        return _observed(
            action,
            remote_id=service,
            exists=exists,
            matches=matches,
            projection={
                "config": current_config.stdout if current_config.returncode == 0 else "",
                "unit": current_unit.stdout if current_unit.returncode == 0 else "",
                "active": active.stdout.strip(),
                "listening": port_listening,
                "version": version.stdout.strip(),
            },
            satisfied=satisfied,
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, RealmHopPayload)
        conn = self.context.connection(payload.server_ref)
        relay_context = RelayContext(
            relay_ip=self.context.address(payload.server_ref),
            exit_ip=self.context.address(payload.target_server_ref),
            exit_port=payload.target_port,
            listen_port=payload.listen_port,
            realm_version=_runtime_pin(self.context.plan, "realm_version", REALM_VERSION),
        )
        installed = InstallRealm().run(conn, relay_context)
        if installed.status == "failed":
            _raise_if_step_timeout(action, installed)
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
            _raise_if_timeout(action, config.result)
            _raise_if_timeout(action, unit.result)
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
            _raise_if_timeout(action, enabled)
            _raise_if_timeout(action, restarted)
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
        NginxArtifactDriver._restore_file(conn, config_path, previous_config)
        NginxArtifactDriver._restore_file(conn, unit_path, previous_unit)


class NodeRuntimeDriver:
    def __init__(self, context: ServerDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _payload(action, NodeRuntimePayload)
        conn = self.context.connection(payload.server_ref)
        compose_path = f"{REMNAWAVE_NODE_DIR}/docker-compose.yml"
        node_image = _runtime_pin(self.context.plan, "remnawave_node_image", REMNAWAVE_NODE_IMAGE)
        expected_compose = render_node_compose(node_image, payload.api_port)
        compose = conn.get_text(compose_path, timeout=15)
        running = conn.run(
            "docker inspect remnawave-node --format '{{.State.Running}}' 2>/dev/null",
            timeout=15,
        )
        node_uuid = self.context.remote_id(payload.binding_ref, action.generation)
        node = self.context.panel.get_node(node_uuid) if node_uuid else None
        warp_ok = True
        if payload.warp:
            warp = conn.run("warp-cli --accept-tos status 2>/dev/null", timeout=15)
            warp_ok = warp.returncode == 0 and "Connected" in warp.stdout
        exists = compose.returncode == 0
        connected = node is not None and node.is_connected
        matches = (
            exists
            and compose.stdout == expected_compose
            and running.returncode == 0
            and running.stdout.strip().lower() == "true"
            and connected
            and warp_ok
        )
        satisfied: set[str] = set()
        if exists:
            satisfied.add("exists")
        if connected:
            satisfied.add("connected")
        return _observed(
            action,
            remote_id=REMNAWAVE_NODE_DIR,
            exists=exists,
            matches=matches,
            projection={
                "compose": compose.stdout if compose.returncode == 0 else "",
                "running": running.stdout.strip(),
                "connected": connected,
                "warp": warp_ok,
            },
            satisfied=satisfied,
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _payload(action, NodeRuntimePayload)
        conn = self.context.connection(payload.server_ref)
        if payload.warp:
            result = InstallWarp().run(
                conn,
                ProvisionContext(
                    ip=self.context.address(payload.server_ref),
                    is_panel_host=False,
                    warp=True,
                ),
            )
            if result.status == "failed":
                raise ResourceReconcileError(f"Could not configure WARP: {result.detail}")
        secret = self.context.node_secrets.get(payload.workload_ref, "")
        if not secret:
            secret = self.context.panel.get_node_secret_key()
        if not secret:
            raise ResourceReconcileError(f"Remnawave returned no node secret for workload {payload.workload_ref!r}.")
        deployed = deploy_node_container(
            conn,
            secret,
            node_api_port=payload.api_port,
            image=_runtime_pin(self.context.plan, "remnawave_node_image", REMNAWAVE_NODE_IMAGE),
        )
        if not deployed:
            raise ResourceReconcileError(f"Remnawave node runtime {payload.workload_ref!r} did not become healthy.")
        return ResourceApplyReceipt(remote_id=REMNAWAVE_NODE_DIR)


def _payload(action: ResourceAction, expected_type: type[PayloadT]) -> PayloadT:
    payload = action.resource.payload
    if not isinstance(payload, expected_type):
        raise ResourceReconcileError(f"Server driver received {payload.kind}, expected {expected_type.__name__}.")
    return payload


def _baseline_context(
    payload: ServerBaselinePayload,
    conn: ServerConnection,
    address: str,
) -> ProvisionContext:
    return ProvisionContext(
        ip=address,
        user=conn.user,
        harden=payload.harden,
        is_panel_host=False,
    )


def _baseline_marker_path(logical_id: str) -> str:
    return f"/var/lib/meridian/baselines/{artifact_token(logical_id)}.attestation"


def _resource_contract_attestation(action: ResourceAction, plan: ResourcePlan) -> str:
    return canonical_hash(
        {
            "resource": action.expected_hash,
            "deployment_contract": plan.deployment_contract.model_dump(mode="json"),
        }
    )


def _runtime_pin(plan: ResourcePlan, name: str, fallback: str) -> str:
    return plan.deployment_contract.runtime_pins.get(name, fallback)


def _observed(
    action: ResourceAction,
    *,
    remote_id: str,
    exists: bool,
    matches: bool,
    projection: object,
    satisfied: set[str],
) -> ResourceObservation:
    keys = sorted(
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
        if condition.kind in satisfied
    )
    return ResourceObservation(
        exists=exists,
        observed_hash=action.expected_hash if matches else canonical_hash(projection),
        remote_id=remote_id,
        satisfied_postconditions=keys,
    )


def _firewall_marker(logical_id: str) -> str:
    return f"meridian-v4-{artifact_token(logical_id)}"


def _firewall_rules(
    logical_id: str,
    payload: FirewallRulePayload,
    addresses: Mapping[str, str],
) -> list[str]:
    marker = _firewall_marker(logical_id)
    q_marker = shlex.quote(marker)
    q_port = shlex.quote(str(payload.port))
    q_transport = shlex.quote(payload.transport)
    if not payload.source_server_refs:
        return [f"allow {q_port}/{q_transport} comment {q_marker}"]
    rules: list[str] = []
    for source_ref in payload.source_server_refs:
        source = addresses.get(source_ref, "")
        if not source:
            raise ResourceReconcileError(f"Firewall source {source_ref} has no resolved address.")
        rules.append(f"allow from {shlex.quote(source)} to any port {q_port} proto {q_transport} comment {q_marker}")
    return rules


def _port_in_ss(output: str, port: int) -> bool:
    suffix = f":{port}"
    return any(field.endswith(suffix) for line in output.splitlines() for field in line.split())


def _raise_if_timeout(action: ResourceAction, result: object | None) -> None:
    if result is not None and getattr(result, "returncode", None) == 124:
        raise UnknownResourceOutcome(f"Timed out while mutating {action.resource.logical_id}; observation is required.")


def _raise_if_step_timeout(action: ResourceAction, result: StepResult) -> None:
    for command in result.commands:
        _raise_if_timeout(action, command)
    if "timed out" in result.detail.casefold() or "timeout" in result.detail.casefold():
        raise UnknownResourceOutcome(f"Timed out while mutating {action.resource.logical_id}; observation is required.")
