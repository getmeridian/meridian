"""Shared CLI presentation for compiled V4 topology plans."""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import require_intent, reviewed_runtime
from meridian.commands.apply_progress import apply_progress
from meridian.console import confirm, err_console, fail, ok, warn
from meridian.core.apply import CompiledApplyPreview, build_compiled_apply_result
from meridian.core.models import MeridianError, OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.plan import CompiledPlanDriftResult, CompiledPlanResourceResult, CompiledPlanResult
from meridian.renderers import emit_json


def run_v4_apply(
    cluster: ClusterConfig,
    *,
    yes: bool,
    json_output: bool,
    operation: OperationContext,
) -> None:
    """Apply compiler-owned V4 topology through the checkpointed executor."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.core.setup import SetupDraft
    from meridian.servers import ServerRegistry

    intent = require_intent(cluster)
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(intent))
    if not yes:
        if json_output:
            error = MeridianError(
                code="MERIDIAN_CONFIRMATION_REQUIRED",
                category="user",
                message="Apply requires explicit confirmation",
                hint="Review data.plan_hash, then pass --yes.",
                retryable=False,
                exit_code=2,
            )
            emit_json(
                command_envelope(
                    command="apply",
                    data=CompiledApplyPreview(
                        plan_hash=review.plan.plan_hash,
                        resource_count=len(review.plan.resources),
                        review_hash=review.review_hash,
                        retiring_resources=[binding.logical_id for binding in review.retiring],
                    ).to_data(),
                    summary=Summary(
                        text=error.message,
                        changed=False,
                    ),
                    status="failed",
                    exit_code=2,
                    errors=[error],
                    timer=operation.timer,
                )
            )
            raise typer.Exit(2)
        err_console.print(
            f"  [bold]V4 topology[/bold]: "
            f"{len(review.plan.resources)} managed resources\n"
            f"  [dim]{review.review_hash}[/dim]"
        )
        for binding in review.retiring:
            err_console.print(f"  [red]remove[/red] {escape(binding.logical_id)}")
        if not confirm("Apply this reviewed topology?"):
            raise typer.Exit(1)

    with apply_progress(machine_output=json_output, registry=registry) as reporter:
        result = reviewed_runtime(registry, cluster, reporter).apply_intent(
            intent, expected_plan_hash=review.review_hash
        )
    status: OutputStatus = "failed" if not result.all_succeeded else "changed" if result.changed else "no_changes"
    exit_code = 0 if result.all_succeeded else 3
    summary_text = "V4 topology converged." if result.all_succeeded else "V4 topology apply failed."
    result_data = build_compiled_apply_result(
        result,
        exit_code=exit_code,
        summary=summary_text,
    )
    apply_error: MeridianError | None = None
    if not result.all_succeeded:
        apply_error = MeridianError(
            code="MERIDIAN_APPLY_FAILED",
            category="system",
            message=summary_text,
            hint="Review failed resources and rerun apply after fixing the underlying issue.",
            retryable=True,
            exit_code=3,
        )
    if json_output:
        emit_json(
            command_envelope(
                command="apply",
                data=result_data.to_data(),
                summary=Summary(
                    text=summary_text,
                    changed=result.changed,
                    counts=result_data.counts.model_dump(),
                ),
                status=status,
                exit_code=exit_code,
                errors=[apply_error] if apply_error else None,
                timer=operation.timer,
            )
        )
        if apply_error:
            raise typer.Exit(3)
    if not result.all_succeeded:
        for item in result.failed:
            warn(f"Failed: {item.action.resource.logical_id} — {item.error}")
        for retirement_error in result.retirement_errors:
            warn(f"Failed: {retirement_error}")
        fail(
            "V4 topology apply failed.",
            hint="Fix the failed resource and rerun `meridian apply`.",
            hint_type="system",
        )
    for logical_id in result.retired:
        ok(f"Removed {logical_id}")
    if result.changed:
        ok("V4 topology applied.")
    else:
        ok("No changes needed — V4 topology is converged.")


def run_v4_plan(
    cluster: ClusterConfig,
    *,
    json_output: bool,
    operation: OperationContext,
) -> None:
    """Render the deterministic compiler graph used by V4 apply."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry
    from meridian.setup.runtime import SetupRuntime

    inspection = SetupRuntime(
        ServerRegistry(SERVER_PROFILES_FILE),
        cluster_loader=lambda: cluster,
    ).inspect_intent(require_intent(cluster))
    resources = [item.action.resource for item in inspection.inspections]
    observation_errors = [item for item in inspection.inspections if item.error]
    drifted = [item for item in inspection.inspections if not item.error and not item.converged]
    converged = (
        inspection.converged and cluster.active_plan_hash == inspection.plan_hash and not cluster.pending_plan_hash
    )
    state_changes: list[str] = []
    if cluster.pending_plan_hash:
        if cluster.pending_plan_hash == inspection.plan_hash:
            state_changes.append(f"activate pending generation {cluster.pending_generation}")
        else:
            state_changes.append("resolve a different pending plan before activating this review")
    elif cluster.active_plan_hash != inspection.plan_hash:
        state_changes.append("activate this reviewed plan as the current generation")
    exit_code = 0 if converged else 2
    counts: dict[str, int] = {}
    for resource in resources:
        resource_kind = resource.payload.kind
        counts[resource_kind] = counts.get(resource_kind, 0) + 1
    if json_output:
        if observation_errors:
            summary_text = (
                f"Could not observe {len(observation_errors)} of {len(resources)} V4 resources; plan is inconclusive."
            )
        elif converged:
            summary_text = "V4 topology is converged."
        elif drifted and state_changes:
            summary_text = (
                f"{len(drifted)} of {len(resources)} V4 resources require repair; "
                "saved generation state also requires activation."
            )
        elif drifted:
            summary_text = f"{len(drifted)} of {len(resources)} V4 resources require repair."
        elif inspection.retiring:
            summary_text = f"{len(inspection.retiring)} resources will be removed."
        else:
            summary_text = "V4 resources match; saved generation state requires activation."
        result_data = CompiledPlanResult(
            plan_hash=inspection.plan_hash,
            converged=converged,
            summary=summary_text,
            exit_code=3 if observation_errors else exit_code,
            state_changes=state_changes,
            warnings=[inspection.fence_warning] if inspection.fence_warning else [],
            observation_errors=[
                CompiledPlanDriftResult(
                    logical_id=item.action.resource.logical_id,
                    error=item.error,
                )
                for item in observation_errors
            ],
            drifted_resources=[
                CompiledPlanDriftResult(
                    logical_id=item.action.resource.logical_id,
                    error=item.error,
                )
                for item in drifted
            ],
            retiring_resources=inspection.retiring,
            review_hash=inspection.review_hash or inspection.plan_hash,
            resources=[
                CompiledPlanResourceResult(
                    logical_id=resource.logical_id,
                    kind=resource.payload.kind,
                    desired_hash=resource.desired_hash,
                    dependencies=resource.dependencies,
                )
                for resource in resources
            ],
        )
        evidence_error = (
            MeridianError(
                code="MERIDIAN_PLAN_EVIDENCE_UNAVAILABLE",
                category="system",
                message=summary_text,
                hint="Restore panel, SSH, and local-state access, then rerun plan.",
                retryable=True,
                exit_code=3,
            )
            if observation_errors
            else None
        )
        emit_json(
            command_envelope(
                command="plan",
                data=result_data.to_data(),
                summary=Summary(
                    text=summary_text,
                    changed=not converged,
                    counts=counts,
                ),
                status="failed" if evidence_error else "no_changes" if converged else "changed",
                exit_code=3 if evidence_error else exit_code,
                errors=[evidence_error] if evidence_error else None,
                timer=operation.timer,
            )
        )
    else:
        state = "[green]converged[/green]" if converged else "[yellow]changes pending[/yellow]"
        reviewed = inspection.review_hash or inspection.plan_hash
        err_console.print(f"\n  [bold]V4 topology[/bold] — {state}\n  [dim]{reviewed}[/dim]\n")
        for kind_name, count in sorted(counts.items()):
            err_console.print(f"  {kind_name}: {count}")
        for item in drifted:
            detail = f" — {item.error}" if item.error else ""
            err_console.print(f"  [yellow]repair[/yellow] {escape(item.action.resource.logical_id)}{escape(detail)}")
        for item in observation_errors:
            err_console.print(
                f"  [yellow]unavailable[/yellow] {escape(item.action.resource.logical_id)} — {escape(item.error)}"
            )
        for logical_id in inspection.retiring:
            err_console.print(f"  [red]remove[/red] {escape(logical_id)}")
        for change in state_changes:
            err_console.print(f"  [yellow]state[/yellow] {escape(change)}")
        if inspection.fence_warning:
            err_console.print(f"\n  [warn]Apply will refuse:[/warn] {escape(inspection.fence_warning)}")
        err_console.print()
    raise typer.Exit(3 if observation_errors else exit_code)
