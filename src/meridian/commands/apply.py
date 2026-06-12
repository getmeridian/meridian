"""Declarative apply command — converge actual state to desired state.

Reads cluster.yml v2, computes a plan, confirms with the user, and
executes. Reuses existing provisioning code via the reconciler executor.
"""

from __future__ import annotations

import shlex
from typing import Any

import typer

from meridian.cluster import ClusterConfig
from meridian.console import confirm, err_console, error_context, fail, info, ok, warn
from meridian.core.apply import build_apply_result
from meridian.core.models import MeridianError, OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.reconciler import PlanActionKind
from meridian.reconciler.diff import PlanAction
from meridian.reconciler.display import print_plan
from meridian.reconciler.executor import ActionHandler, ExecutionResult, execute_plan
from meridian.reconciler.prepare import compute_reconciliation_plan, validate_cluster_for_reconciliation
from meridian.remnawave import MeridianPanel
from meridian.renderers import emit_json


def _looks_like_ip(s: str) -> bool:
    """Cheap guard for the UPDATE_RELAY exit-node resolution path.

    Used only to distinguish "user wrote an IP literally" from "user wrote a
    node name that doesn't exist in the cluster".
    """
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _handle_add_node(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Provision and register a new node."""
    from meridian.operations import add_node

    # Find the desired node spec to get SSH/domain/sni params
    desired = next((n for n in (cluster.desired_nodes or []) if n.host == action.target), None)
    add_node(
        cluster,
        panel,
        ip=action.target,
        ssh_user=desired.ssh_user if desired else "root",
        ssh_port=desired.ssh_port if desired else 22,
        name=desired.name if desired else "",
        domain=desired.domain if desired else "",
        sni=desired.sni if desired else "",
        warp=desired.warp if desired else None,
    )


def _handle_update_node(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Redeploy a node with updated configuration."""
    from meridian.operations import update_node

    desired = next((n for n in (cluster.desired_nodes or []) if n.host == action.target), None)
    update_node(
        cluster,
        panel,
        ip=action.target,
        name=desired.name if desired and desired.name else None,
        sni=desired.sni if desired else None,
        domain=desired.domain if desired else None,
        warp=desired.warp if desired else None,
    )


def _handle_remove_node(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Deregister and remove a node."""
    from meridian.operations import remove_node

    remove_node(cluster, panel, node_ip=action.target)


def _handle_add_relay(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Provision and register a new relay."""
    from meridian.operations import add_relay

    desired = next((r for r in (cluster.desired_relays or []) if r.host == action.target), None)
    # Resolve exit_node name → IP (desired.exit_node can be a name or IP)
    exit_node_value = desired.exit_node if desired else ""
    if exit_node_value:
        exit_node_entry = cluster.find_node(exit_node_value)
        if exit_node_entry:
            exit_node_value = exit_node_entry.ip
    add_relay(
        cluster,
        panel,
        relay_ip=action.target,
        exit_node_ip=exit_node_value,
        ssh_user=desired.ssh_user if desired else "root",
        ssh_port=desired.ssh_port if desired else 22,
        name=desired.name or desired.host if desired else "",
        sni=desired.sni if desired else "",
    )


def _handle_update_relay(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Update a relay by removing the old config and re-provisioning with the new one.

    Failure semantics: this action is destructive — once the old relay is
    removed, traffic for clients pointing to it stops. To keep the window as
    short as possible (and avoid leaving the cluster with no relay at all when
    the new config is unreachable), we preflight the new exit node + new SSH
    target before we touch the existing relay. If preflight fails we leave
    the old relay alone and surface a RuntimeError.

    NOTE: this is best-effort safety. A successful preflight does not
    guarantee that the subsequent provisioning will succeed — but it catches
    the common cases (panel cannot reach new exit node, SSH cannot reach new
    relay host) before we do any irreversible work.
    """
    from meridian.operations import add_relay, remove_relay
    from meridian.ssh import ServerConnection

    desired = next(
        (r for r in (cluster.desired_relays or []) if r.host == action.target),
        None,
    )
    if desired is None:
        # Nothing to update to — the diff should not have produced this case,
        # but be defensive and avoid wiping the existing relay for nothing.
        raise RuntimeError(
            f"UPDATE_RELAY for {action.target} but no matching desired relay in cluster.yml — refusing to delete"
        )

    # Resolve exit_node name → IP. If desired.exit_node is already an IP
    # (no matching node entry by that name), leave it as-is — but if it
    # looks like a name and there is no such node, refuse to proceed.
    exit_node_value = desired.exit_node or ""
    resolved = False
    if exit_node_value:
        exit_node_entry = cluster.find_node(exit_node_value)
        if exit_node_entry:
            exit_node_value = exit_node_entry.ip
            resolved = True
        elif _looks_like_ip(exit_node_value):
            resolved = True

    if not exit_node_value or not resolved:
        raise RuntimeError(
            f"UPDATE_RELAY for {action.target}: desired exit_node "
            f"'{desired.exit_node}' could not be resolved to an IP — refusing to delete old relay"
        )

    # --- Preflight: SSH connectivity to the new relay host ---
    # If we can't even open a session to the new target, there is no point
    # tearing down the old relay first.
    try:
        ssh_check = ServerConnection(action.target, desired.ssh_user, port=desired.ssh_port)
        result = ssh_check.run("true", timeout=15)
        if result.returncode != 0:
            raise RuntimeError(
                f"SSH preflight to {desired.ssh_user}@{action.target}:{desired.ssh_port} "
                f"returned exit {result.returncode} — refusing to remove the running relay"
            )
    except Exception as e:
        raise RuntimeError(f"UPDATE_RELAY preflight failed for {action.target}: {e}. Old relay left intact.") from e

    # Preflight passed — proceed with the destructive swap.
    remove_relay(cluster, panel, relay_ip=action.target)
    add_relay(
        cluster,
        panel,
        relay_ip=action.target,
        exit_node_ip=exit_node_value,
        ssh_user=desired.ssh_user,
        ssh_port=desired.ssh_port,
        name=desired.name or desired.host,
        sni=desired.sni,
    )


def _handle_remove_relay(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Remove a relay."""
    from meridian.operations import remove_relay

    remove_relay(cluster, panel, relay_ip=action.target)


def _handle_add_client(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Create a client via the panel API."""
    from meridian.operations import add_client

    add_client(cluster, panel, name=action.target)


def _handle_remove_client(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Delete a client via the panel API."""
    from meridian.operations import remove_client

    remove_client(cluster, panel, name=action.target)


def _handle_add_subscription_page(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Deploy the subscription page container.

    Handles both fresh deploys (container not in compose) and restarts
    (container exists but stopped). Regenerates docker-compose.yml if the
    subscription page service is missing, then configures with API token.
    """
    from meridian.config import (
        REMNAWAVE_BACKEND_IMAGE,
        REMNAWAVE_PANEL_DIR,
        REMNAWAVE_PANEL_PORT,
        REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
        REMNAWAVE_SUBSCRIPTION_PAGE_PORT,
    )
    from meridian.provision.remnawave_panel import (
        _render_panel_compose,
        configure_subscription_page,
    )
    from meridian.ssh import ServerConnection

    if not cluster.panel.server_ip:
        raise RuntimeError("Panel server IP not set in cluster config")

    conn = ServerConnection(cluster.panel.server_ip, cluster.panel.ssh_user, port=cluster.panel.ssh_port)

    # Check if subscription page container exists in docker-compose
    check = conn.run(
        "docker compose config --services 2>/dev/null | grep -q subscription",
        cwd=REMNAWAVE_PANEL_DIR,
        timeout=15,
    )
    if check.returncode != 0:
        # Container not in compose — regenerate compose file, create placeholder
        # .env.subscription (required by compose), then bring up
        compose = _render_panel_compose(
            image=REMNAWAVE_BACKEND_IMAGE,
            panel_port=REMNAWAVE_PANEL_PORT,
            subscription_page_image=REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
            subscription_page_host_port=REMNAWAVE_SUBSCRIPTION_PAGE_PORT,
        )
        compose_path = f"{REMNAWAVE_PANEL_DIR}/docker-compose.yml"
        result = conn.put_text(
            compose_path,
            compose,
            mode="644",
            timeout=15,
            operation_name="write remnawave panel compose",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to write docker-compose.yml: {result.stderr.strip()[:200]}")

        # Create placeholder .env.subscription so docker compose up doesn't fail
        from meridian.provision.remnawave_panel import _render_subscription_env

        sub_env = _render_subscription_env()
        sub_env_path = f"{REMNAWAVE_PANEL_DIR}/.env.subscription"
        result = conn.put_text(
            sub_env_path,
            sub_env,
            mode="600",
            sensitive=True,
            timeout=15,
            operation_name="write subscription page env",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to write .env.subscription: {result.stderr.strip()[:200]}")

        # --no-recreate: the panel and backend are already running — we only
        # want to bring up the newly-added subscription-page service. Without
        # this flag, any unrelated config drift in compose (image tag bump,
        # env churn) would recreate the panel container and cause a visible
        # outage during what should be a surgical subpage rollout.
        result = conn.run("docker compose up -d --no-recreate", cwd=REMNAWAVE_PANEL_DIR, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start containers: {result.stderr.strip()[:200]}")
    else:
        # Service exists in compose but may be stopped (e.g. from a prior
        # REMOVE_SUBSCRIPTION_PAGE that ran `docker compose stop`). Bring it
        # up — `docker compose up -d <service>` is idempotent and a no-op
        # when the container is already running.
        result = conn.run(
            "docker compose up -d remnawave-subscription-page",
            cwd=REMNAWAVE_PANEL_DIR,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start subscription page container: {result.stderr.strip()[:200]}")

    if not configure_subscription_page(conn, cluster.panel.api_token):
        raise RuntimeError("Failed to configure subscription page")

    # Ensure nginx has a proxy route for the subscription page.
    # Generate a random path if none exists (same approach as provisioner).
    sub_path = cluster.subscription_page.path if cluster.subscription_page else ""
    if not sub_path:
        import secrets

        sub_path = secrets.token_hex(8)

    # Validate sub_path to prevent shell injection / nginx config corruption
    # (normally auto-generated by token_hex, but cluster.yml is user-editable).
    import re as _re

    if not _re.match(r"^[a-zA-Z0-9_-]+$", sub_path):
        raise RuntimeError(
            f"Invalid subscription page path: {sub_path!r}. "
            "Must be alphanumeric/dash/underscore only (auto-generated paths always are)."
        )

    # Inject subscription page nginx location into the existing server block.
    # Check if already configured, skip if so.
    from meridian.config import REMNAWAVE_SUBSCRIPTION_PAGE_PORT

    check_existing = conn.run(
        f"grep -q {shlex.quote(sub_path)} /etc/nginx/conf.d/meridian-http.conf 2>/dev/null",
        timeout=15,
    )
    if check_existing.returncode != 0:
        # The location block must go inside the server{} block in meridian-http.conf.
        # Insert before the "# Root:" comment (always present in rendered config).
        # START/END markers enable reliable sed deletion in _handle_remove_subscription_page.
        location_block = (
            f"        # --- BEGIN Subscription Page (managed by meridian apply) ---\\n"
            f"        location /{sub_path}/ {{\\n"
            f"            proxy_pass http://127.0.0.1:{REMNAWAVE_SUBSCRIPTION_PAGE_PORT}/;\\n"
            f"            proxy_http_version 1.1;\\n"
            f"            proxy_set_header Host \\$host;\\n"
            f"            proxy_set_header X-Real-IP \\$remote_addr;\\n"
            f"            proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;\\n"
            f"            proxy_set_header X-Forwarded-Proto \\$scheme;\\n"
            f"        }}\\n"
            f"        # --- END Subscription Page ---\\n"
        )
        conn.run(
            f"sed -i '/# Root:/i\\{location_block}' /etc/nginx/conf.d/meridian-http.conf",
            timeout=15,
        )
        result = conn.run("nginx -t 2>&1", timeout=15)
        if result.returncode == 0:
            conn.run("systemctl reload nginx", timeout=15)
        else:
            raise RuntimeError(f"nginx validation failed after adding subscription page: {result.stdout.strip()[:200]}")

    # Initialize subscription_page config if missing — apply was triggered by
    # a desired enable, so the config must exist after this handler runs.
    if cluster.subscription_page is None:
        from meridian.cluster import SubscriptionPageConfig

        cluster.subscription_page = SubscriptionPageConfig()
    cluster.subscription_page.enabled = True
    cluster.subscription_page.path = sub_path
    cluster.subscription_page.deployed = True
    cluster.save()


def _handle_remove_subscription_page(action: PlanAction, panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Stop the subscription page container."""
    from meridian.config import REMNAWAVE_PANEL_DIR
    from meridian.ssh import ServerConnection

    if not cluster.panel.server_ip:
        raise RuntimeError("Panel server IP not set in cluster config")

    conn = ServerConnection(cluster.panel.server_ip, cluster.panel.ssh_user, port=cluster.panel.ssh_port)
    result = conn.run("docker compose stop remnawave-subscription-page", cwd=REMNAWAVE_PANEL_DIR, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to stop subscription page: {result.stderr.strip()[:200]}")

    # Remove nginx location block for subscription page.
    # Uses START/END markers injected by _handle_add_subscription_page —
    # safer than matching a single pattern + range-to-}}.
    sub_path = cluster.subscription_page.path if cluster.subscription_page else ""
    if sub_path:
        conn.run(
            f"grep -q {shlex.quote(sub_path)} /etc/nginx/conf.d/meridian-http.conf 2>/dev/null"
            f" && sed -i '/BEGIN Subscription Page/,/END Subscription Page/d' /etc/nginx/conf.d/meridian-http.conf",
            timeout=15,
        )
        result = conn.run("nginx -t 2>&1", timeout=15)
        if result.returncode == 0:
            conn.run("systemctl reload nginx", timeout=15)
        else:
            raise RuntimeError(
                f"nginx validation failed after removing subscription page: {result.stdout.strip()[:200]}"
            )

    if cluster.subscription_page is not None:
        cluster.subscription_page.enabled = False
        cluster.subscription_page.deployed = False
    cluster.save()


def _emit_apply_json(
    *,
    plan: Any,
    execution_result: ExecutionResult,
    exit_code: int,
    status: OutputStatus,
    operation: OperationContext,
    error: MeridianError | None = None,
    summary: str | None = None,
    all_succeeded: bool | None = None,
) -> None:
    """Emit a typed apply envelope."""
    result = build_apply_result(
        plan,
        execution_result,
        exit_code=exit_code,
        summary=summary,
        all_succeeded=all_succeeded,
    )
    emit_json(
        command_envelope(
            command="apply",
            data=result.to_data(),
            summary=Summary(
                text=result.summary,
                changed=status == "changed",
                counts=result.counts.model_dump(),
            ),
            status=status,
            exit_code=exit_code,
            errors=[error] if error else None,
            timer=operation.timer,
        )
    )


def _emit_apply_confirmation_required(
    *,
    plan: Any,
    operation: OperationContext,
    message: str = "Apply requires explicit confirmation",
) -> None:
    """Emit a non-interactive apply error that still includes the computed plan."""
    error = MeridianError(
        code="MERIDIAN_CONFIRMATION_REQUIRED",
        category="user",
        message=message,
        hint="Pass --yes after reviewing data.plan, and use --prune-extras=yes only when drift should be removed.",
        retryable=False,
        exit_code=2,
    )
    _emit_apply_json(
        plan=plan,
        execution_result=ExecutionResult(),
        exit_code=2,
        status="failed",
        operation=operation,
        error=error,
        summary=message,
        all_succeeded=False,
    )


def _emit_apply_drift_decision_required(*, plan: Any, operation: OperationContext) -> None:
    """Emit a non-interactive apply error when drift handling was not explicit."""
    message = "Apply requires an explicit drift decision"
    error = MeridianError(
        code="MERIDIAN_DRIFT_DECISION_REQUIRED",
        category="user",
        message=message,
        hint="Pass --prune-extras=no to keep drift, or --prune-extras=yes to remove it.",
        retryable=False,
        exit_code=2,
    )
    _emit_apply_json(
        plan=plan,
        execution_result=ExecutionResult(),
        exit_code=2,
        status="failed",
        operation=operation,
        error=error,
        summary=message,
        all_succeeded=False,
    )


def _execute_plan_quietly_for_json(
    plan: Any,
    *,
    panel: MeridianPanel,
    cluster: ClusterConfig,
    callbacks: dict[PlanActionKind, ActionHandler],
    max_parallel: int,
) -> ExecutionResult:
    """Run CLI-backed callbacks without letting nested fail() write JSON fragments."""
    from meridian.console import is_json_mode, set_json_mode

    previous_json_mode = is_json_mode()
    set_json_mode(False)
    try:
        return execute_plan(plan, panel=panel, cluster=cluster, callbacks=callbacks, max_parallel=max_parallel)
    finally:
        set_json_mode(previous_json_mode)


def run(
    yes: bool = False,
    parallel: int = 4,
    prune_extras: str = "ask",
    json_output: bool = False,
) -> None:
    """Converge actual state to desired state declared in cluster.yml."""
    operation = OperationContext()
    with error_context("apply", timer=operation.timer):
        _run(yes=yes, parallel=parallel, prune_extras=prune_extras, json_output=json_output, operation=operation)


def _run(
    yes: bool,
    parallel: int,
    prune_extras: str,
    *,
    json_output: bool,
    operation: OperationContext,
) -> None:
    """Converge actual state to desired state declared in cluster.yml.

    ``prune_extras`` controls how panel-side resources missing from
    cluster.yml (i.e. drift, marked ``from_extras=True`` on the plan
    actions) are handled:

    - ``"ask"``: interactive prompt per extras action. If combined with
      ``yes=True``, downgraded to ``"no"`` for safety — auto-prune
      requires explicit consent.
    - ``"yes"``: auto-remove (current behaviour).
    - ``"no"``: skip extras (filtered out of the plan before execute).
    """
    cluster = ClusterConfig.load()
    validate_cluster_for_reconciliation(cluster, "apply")

    info("Fetching actual state from panel...")
    from meridian.remnawave import MeridianPanel, RemnawaveError
    from meridian.ssh import ServerConnection

    try:
        # SSH connection to panel host for live subscription page check
        panel_conn = None
        if cluster.panel.server_ip:
            panel_conn = ServerConnection(cluster.panel.server_ip, cluster.panel.ssh_user, port=cluster.panel.ssh_port)

        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            plan = compute_reconciliation_plan(cluster, panel, panel_conn=panel_conn)

            if plan.is_empty:
                if json_output:
                    _emit_apply_json(
                        plan=plan,
                        execution_result=ExecutionResult(),
                        exit_code=0,
                        status="no_changes",
                        operation=operation,
                    )
                ok("No changes needed — infrastructure is up to date.")
                raise typer.Exit(0)

            if not json_output:
                print_plan(plan, console=err_console)

            # --- Drift / extras handling (--prune-extras) ---
            extras_actions = [a for a in plan.actions if a.from_extras]
            effective_prune = prune_extras
            if yes and effective_prune == "ask":
                # Safety: never silently auto-remove drift under --yes unless
                # the operator explicitly asked for `--prune-extras=yes`.
                effective_prune = "no"

            if extras_actions:
                if json_output and prune_extras == "ask":
                    _emit_apply_drift_decision_required(plan=plan, operation=operation)
                    raise typer.Exit(2)
                if effective_prune == "no":
                    plan.actions = [a for a in plan.actions if not a.from_extras]
                    info(
                        f"Skipping {len(extras_actions)} extras action(s) — "
                        f"resources present on the panel but not in cluster.yml. "
                        f"Use --prune-extras=yes to remove them, or add them to cluster.yml."
                    )
                elif effective_prune == "ask":
                    keep: list = []
                    for action in plan.actions:
                        if not action.from_extras:
                            keep.append(action)
                            continue
                        if confirm(f"Remove {action.kind.value} {action.target}? (drift — not in cluster.yml)"):
                            keep.append(action)
                        else:
                            info(f"Skipping {action.kind.value} {action.target}")
                    plan.actions = keep
                # effective_prune == "yes": leave plan untouched, extras run
                if plan.is_empty:
                    if json_output:
                        _emit_apply_json(
                            plan=plan,
                            execution_result=ExecutionResult(),
                            exit_code=0,
                            status="no_changes",
                            operation=operation,
                        )
                    ok("No changes needed after extras filter.")
                    raise typer.Exit(0)

            if plan.has_destructive and not yes:
                if json_output:
                    _emit_apply_confirmation_required(plan=plan, operation=operation)
                    raise typer.Exit(2)
                if not confirm("Plan includes destructive actions. Apply?"):
                    raise typer.Exit(1)
            elif not yes:
                if json_output:
                    _emit_apply_confirmation_required(plan=plan, operation=operation)
                    raise typer.Exit(2)
                if not confirm("Apply this plan?"):
                    raise typer.Exit(1)

            info("Applying plan...")
            callbacks: dict[PlanActionKind, ActionHandler] = {
                PlanActionKind.ADD_NODE: _handle_add_node,
                PlanActionKind.UPDATE_NODE: _handle_update_node,
                PlanActionKind.REMOVE_NODE: _handle_remove_node,
                PlanActionKind.ADD_RELAY: _handle_add_relay,
                PlanActionKind.UPDATE_RELAY: _handle_update_relay,
                PlanActionKind.REMOVE_RELAY: _handle_remove_relay,
                PlanActionKind.ADD_CLIENT: _handle_add_client,
                PlanActionKind.REMOVE_CLIENT: _handle_remove_client,
                PlanActionKind.ADD_SUBSCRIPTION_PAGE: _handle_add_subscription_page,
                PlanActionKind.REMOVE_SUBSCRIPTION_PAGE: _handle_remove_subscription_page,
            }

            if json_output:
                result = _execute_plan_quietly_for_json(
                    plan,
                    panel=panel,
                    cluster=cluster,
                    callbacks=callbacks,
                    max_parallel=parallel,
                )
            else:
                result = execute_plan(plan, panel=panel, cluster=cluster, callbacks=callbacks, max_parallel=parallel)

            # Snapshot desired state for next plan's from_extras classification.
            # Only snapshot when ALL actions succeeded — partial failures should
            # leave the old snapshot so the next apply retries correctly. And
            # only snapshot categories that are actively managed (not None) to
            # avoid erasing history when a category is temporarily unmanaged.
            if result.all_succeeded:
                if cluster.desired_clients is not None:
                    cluster.applied_state.clients = list(cluster.desired_clients)
                if cluster.desired_nodes is not None:
                    cluster.applied_state.nodes = [n.host for n in cluster.desired_nodes]
                if cluster.desired_relays is not None:
                    cluster.applied_state.relays = [r.host for r in cluster.desired_relays]

            try:
                cluster.save()
            except Exception as exc:
                error = MeridianError(
                    code="MERIDIAN_STATE_SAVE_FAILED",
                    category="system",
                    message=f"Apply completed but Meridian could not save cluster state: {exc}",
                    hint="Resolve the local state file issue, then rerun plan/apply to reconcile.",
                    retryable=True,
                    exit_code=3,
                )
                if json_output:
                    _emit_apply_json(
                        plan=plan,
                        execution_result=result,
                        exit_code=3,
                        status="failed",
                        operation=operation,
                        error=error,
                        summary=error.message,
                        all_succeeded=False,
                    )
                    raise typer.Exit(3) from exc
                fail(error.message, hint=error.hint, hint_type="system")

            if result.all_succeeded:
                if json_output:
                    _emit_apply_json(
                        plan=plan,
                        execution_result=result,
                        exit_code=0,
                        status="changed",
                        operation=operation,
                    )
                ok(result.summary())
            else:
                error = MeridianError(
                    code="MERIDIAN_APPLY_FAILED",
                    category="system",
                    message=result.summary(),
                    hint="Review failed actions and rerun apply after fixing the underlying issue.",
                    retryable=True,
                    exit_code=3,
                )
                if json_output:
                    _emit_apply_json(
                        plan=plan,
                        execution_result=result,
                        exit_code=3,
                        status="failed",
                        operation=operation,
                        error=error,
                    )
                    raise typer.Exit(3)
                for failed_action in result.failed:
                    warn(f"Failed: {failed_action.action.detail} — {failed_action.error}")
                fail(result.summary(), hint_type="system")

    except RemnawaveError as e:
        fail(f"Panel API error: {e}", hint_type="system")
