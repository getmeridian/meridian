"""SSH-backed drivers for reviewed V4 server resources."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    CertificatePayload,
    FirewallRulePayload,
    NginxArtifactPayload,
    ResourcePlan,
    ServerBaselinePayload,
)
from meridian.core.cancellation import CancellationToken
from meridian.core.inputs import is_ip
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.provision.baseline import build_server_baseline_checks, build_server_baseline_steps
from meridian.provision.ensure import ensure_file_content, ensure_ufw_rule, ufw_rule_present
from meridian.provision.nginx import InstallNginx
from meridian.provision.steps import ProvisionContext
from meridian.provision.tls import IssueTLSCert
from meridian.reconciler import server_retirement as retirement
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceDrivers,
    ResourceObservation,
    ResourceReconcileError,
    payload_of,
)
from meridian.reconciler.server_evidence import (
    assertion_matches,
    baseline_attestation,
    baseline_check_command,
    build_observation,
    content_mismatch,
    file_content_or_absent,
    listener_wait_command,
    negated_path_is_absent,
    port_is_listening,
    require_successful_evidence,
    service_is_active,
)
from meridian.reconciler.server_render import (
    artifact_token,
    baseline_marker_path,
    certificate_directory,
    firewall_marker,
    legacy_baseline_marker_path,
    legacy_firewall_marker,
    nginx_artifact_path,
    render_firewall_rules,
    render_nginx_artifact,
)
from meridian.reconciler.server_support import (
    raise_if_step_timeout,
    raise_if_timeout,
    restore_file,
)
from meridian.ssh import ServerConnection

ConnectionFactory = Callable[[str], ServerConnection]
_payload = payload_of
_raise_if_timeout = raise_if_timeout
_raise_if_step_timeout = raise_if_step_timeout


@dataclass
class ServerDriverContext:
    plan: ResourcePlan
    cluster: ClusterConfig
    connection_for: ConnectionFactory
    server_addresses: Mapping[str, str]
    cancellation: CancellationToken | None = None
    reporter: Reporter = field(default_factory=NoopReporter)
    operation: OperationContext | None = None

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
    from meridian.reconciler.realm_driver import RealmHopDriver

    return {
        "server_baseline": ServerBaselineDriver(context),
        "firewall_rule": FirewallRuleDriver(context),
        "certificate": CertificateDriver(context),
        "nginx_artifact": NginxArtifactDriver(context),
        "realm_hop": RealmHopDriver(context),
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
        )
        outcomes = {
            check.name: assertion_matches(
                action.resource.logical_id,
                conn.run(baseline_check_command(check.name, check.command), timeout=15),
                f"baseline check {check.name}",
                negative_returncodes=(1, 3, 4) if check.name == "fail2ban" else (1,),
            )
            for check in checks
        }
        marker = conn.get_text(baseline_marker_path(action.resource.logical_id), timeout=15)
        marker_content = file_content_or_absent(action.resource.logical_id, marker, "baseline attestation")
        attestation = baseline_attestation(action, self.context.plan, payload.server_ref)
        marker_ok = marker_content == f"{attestation}\n"
        matches = marker_ok and all(outcomes.values())
        mismatches = [f"baseline check '{name}' fails" for name, ok in outcomes.items() if not ok]
        if not marker_ok:
            mismatches.append("the attestation is missing" if marker_content is None else "the attestation is stale")
        return build_observation(
            action,
            remote_id=payload.server_ref,
            exists=marker_content is not None,
            matches=matches,
            projection={
                "attestation": marker_content or "",
                "checks": outcomes,
            },
            satisfied={"exists"} if matches else set(),
            mismatches=mismatches,
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
            )
        ).run(
            conn,
            context,
            reporter=self.context.reporter,
            operation=self.context.operation,
            cancellation=self.context.cancellation,
        )
        failures = [result for result in results if result.status == "failed"]
        if failures:
            failure = failures[0]
            _raise_if_step_timeout(action, failure)
            raise ResourceReconcileError(
                f"Could not prepare server {payload.server_ref}: {failure.name}: {failure.detail}"
            )
        attestation = ensure_file_content(
            conn,
            baseline_marker_path(action.resource.logical_id),
            f"{baseline_attestation(action, self.context.plan, payload.server_ref)}\n",
            mode="600",
            create_parent=True,
        )
        if not attestation.ok:
            _raise_if_timeout(action, attestation.result)
            raise ResourceReconcileError(f"Could not attest server baseline {payload.server_ref}: {attestation.detail}")
        legacy = legacy_baseline_marker_path(action.resource.logical_id)
        # The control-runtime attestation may still live beside it, so the rmdir may fail harmlessly.
        removed = conn.run(
            f"rm -f {shlex.quote(legacy)}"
            " && { rmdir /var/lib/meridian/baselines /var/lib/meridian 2>/dev/null; true; }",
            timeout=15,
        )
        if removed.returncode != 0:
            _raise_if_timeout(action, removed)
            raise ResourceReconcileError(f"Could not remove the legacy baseline attestation {legacy}.")
        return ResourceApplyReceipt(remote_id=payload.server_ref)


# Teardown stops and disables nginx but leaves it installed, and plain
# `reload` fails on an inactive unit; start it if it is not running.
NGINX_ENABLE_AND_RELOAD = "systemctl enable nginx >/dev/null 2>&1; systemctl reload-or-restart nginx"


class FirewallRuleDriver(retirement.FirewallRetirement):
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
        require_successful_evidence(action.resource.logical_id, result, "UFW rule inspection")
        rules = render_firewall_rules(action.resource.logical_id, payload, self.context.server_addresses)
        present = all(ufw_rule_present(result.stdout, rule) for rule in rules)
        matches = present and legacy_firewall_marker(action.resource.logical_id) not in result.stdout
        return build_observation(
            action,
            remote_id=firewall_marker(action.resource.logical_id),
            exists=matches,
            matches=matches,
            projection={"rules": result.stdout.splitlines()},
            satisfied={"exists"} if matches else set(),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        logical_id = action.resource.logical_id
        payload = _payload(action, FirewallRulePayload)
        conn = self.context.connection(payload.server_ref)
        rules = render_firewall_rules(logical_id, payload, self.context.server_addresses)
        self._ensure_rules(action, conn, rules)
        legacy = legacy_firewall_marker(logical_id)
        added = conn.run("ufw show added 2>/dev/null", timeout=15)
        _raise_if_timeout(action, added)
        if added.returncode == 0 and legacy in added.stdout:
            # Pre-rename rules: UFW may have skipped the renamed copy as a duplicate,
            # so delete the old comment, then re-add; restore the old rules on failure.
            if not retirement.delete_rules_by_comment(conn, legacy):
                raise ResourceReconcileError(f"Could not remove the legacy firewall rule {logical_id}.")
            try:
                self._ensure_rules(action, conn, rules)
            except ResourceReconcileError:
                for rule in render_firewall_rules(logical_id, payload, self.context.server_addresses, marker=legacy):
                    ensure_ufw_rule(conn, rule)
                raise
        return ResourceApplyReceipt(remote_id=firewall_marker(logical_id))

    @staticmethod
    def _ensure_rules(action: ResourceAction, conn: ServerConnection, rules: list[str]) -> None:
        for rule in rules:
            result = ensure_ufw_rule(conn, rule)
            if not result.ok:
                _raise_if_timeout(action, result.result)
                raise ResourceReconcileError(
                    f"Could not apply firewall rule {action.resource.logical_id}: {result.detail}"
                )


class CertificateDriver(retirement.CertificateRetirement):
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
        certificate = file_content_or_absent(
            action.resource.logical_id,
            conn.get_text(certificate_path, timeout=15),
            "certificate file",
        )
        if certificate is None:
            return build_observation(
                action,
                remote_id=certificate_path,
                exists=False,
                matches=False,
                projection={"certificate": ""},
                satisfied=set(),
            )
        result = conn.run(
            f"openssl x509 -in {shlex.quote(certificate_path)} -noout -checkend 86400 -ext subjectAltName 2>/dev/null",
            timeout=15,
        )
        valid = assertion_matches(action.resource.logical_id, result, "certificate validation")
        san = f"IP Address:{payload.hostname}" if is_ip(payload.hostname) else f"DNS:{payload.hostname}"
        matches = valid and san in result.stdout
        mismatches = [] if valid else ["the certificate expires within a day"]
        if san not in result.stdout:
            mismatches.append(f"the certificate does not name {payload.hostname}")
        return build_observation(
            action,
            remote_id=certificate_path,
            exists=True,
            matches=matches,
            projection={"certificate": result.stdout},
            satisfied={"exists"},
            mismatches=mismatches,
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
            ),
        )
        if installed.status == "failed":
            raise ResourceReconcileError(f"Could not install nginx: {installed.detail}")
        challenge_path = retirement.ACME_CHALLENGE_PATH
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
        enabled_path = challenge_path.replace("/sites-available/", "/sites-enabled/")
        linked = conn.run(
            f"rm -f /etc/nginx/sites-enabled/default && "
            f"ln -sfn {shlex.quote(challenge_path)} {shlex.quote(enabled_path)}",
            timeout=15,
        )
        validation = conn.run("nginx -t 2>&1", timeout=15)
        if linked.returncode != 0 or validation.returncode != 0:
            _raise_if_timeout(action, linked)
            raise ResourceReconcileError("Could not start the ACME HTTP-01 challenge listener.")
        reload_result = conn.run(NGINX_ENABLE_AND_RELOAD, timeout=30)
        if reload_result.returncode != 0:
            _raise_if_timeout(action, reload_result)
            raise ResourceReconcileError("Could not start the ACME HTTP-01 challenge listener.")
        cert_dir = certificate_directory(payload.hostname)
        bare_ip = is_ip(payload.hostname)
        result = IssueTLSCert(
            payload.hostname,
            # Let's Encrypt's short-lived profile is the only one that certifies an IP address.
            ip_mode=bare_ip,
            server_ip=payload.hostname if bare_ip else None,
            certificate_directory=cert_dir,
        ).run(
            conn,
            ProvisionContext(
                ip=self.context.address(payload.server_ref),
                domain=payload.hostname,
            ),
        )
        if result.status == "failed":
            raise ResourceReconcileError(f"Could not issue TLS certificate for {payload.hostname}: {result.detail}")
        return ResourceApplyReceipt(remote_id=f"{cert_dir}/fullchain.pem")


class NginxArtifactDriver(retirement.NginxArtifactRetirement):
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
        current_content = file_content_or_absent(action.resource.logical_id, current, "nginx artifact")
        legacy_absent = True
        if payload.layer == "stream" and payload.listener_port == 443:
            legacy = conn.run(
                f"test ! -e {shlex.quote(self._LEGACY_STREAM_PATH)}",
                timeout=15,
            )
            legacy_absent = negated_path_is_absent(
                action.resource.logical_id,
                legacy,
                "legacy nginx artifact",
            )
        active = conn.run("systemctl is-active nginx", timeout=15)
        listening = conn.run("ss -H -lnt 2>/dev/null", timeout=15)
        service_active = service_is_active(action.resource.logical_id, active, "nginx service")
        require_successful_evidence(action.resource.logical_id, listening, "TCP listener inspection")
        file_matches = current_content == expected
        port_listening = port_is_listening(listening.stdout, payload.listener_port)
        matches = file_matches and service_active and port_listening and legacy_absent
        mismatches = [
            reason
            for reason, failed in (
                (content_mismatch(path, current_content, expected), not file_matches),
                ("nginx is not active", not service_active),
                (f"nothing listens on TCP port {payload.listener_port}", not port_listening),
                (f"{self._LEGACY_STREAM_PATH} is still present", not legacy_absent),
            )
            if failed
        ]
        satisfied: set[str] = set()
        if current_content is not None:
            satisfied.add("exists")
        if port_listening:
            satisfied.add("listening")
        return build_observation(
            action,
            remote_id=path,
            exists=current_content is not None,
            matches=matches,
            projection={
                "content": current_content or "",
                "active": service_active,
                "listening": port_listening,
                "legacy_absent": legacy_absent,
            },
            satisfied=satisfied,
            mismatches=mismatches,
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
            ProvisionContext(ip=address),
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
        reload_result = conn.run(NGINX_ENABLE_AND_RELOAD, timeout=30)
        if reload_result.returncode != 0:
            _raise_if_timeout(action, reload_result)
            self._restore_artifact(conn, path, previous_content, enabled)
            if owns_legacy_stream:
                self._restore_file(conn, self._LEGACY_STREAM_PATH, legacy_content)
            conn.run(NGINX_ENABLE_AND_RELOAD, timeout=30)
            raise ResourceReconcileError(f"nginx reload failed for {path}.")
        # Reload hands new listeners over asynchronously; observing at once can miss the port
        # on a busy 1 vCPU server. A port that never comes up is the observation's to report.
        conn.run(listener_wait_command(payload.listener_port), timeout=20)
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
        restore_file(conn, path, previous_content)

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


def _baseline_context(
    payload: ServerBaselinePayload,
    conn: ServerConnection,
    address: str,
) -> ProvisionContext:
    return ProvisionContext(
        ip=address,
        user=conn.user,
        harden=payload.harden,
    )
