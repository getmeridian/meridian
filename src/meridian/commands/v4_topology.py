"""Shared CLI presentation for compiled V4 topology plans."""

from __future__ import annotations

import typer

from meridian.cluster import ClusterConfig
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
    from meridian.setup.runtime import SetupRuntime

    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("V4 topology intent is missing")
    runtime = SetupRuntime(
        ServerRegistry(SERVER_PROFILES_FILE),
        cluster_loader=lambda: cluster,
    )
    draft = SetupDraft.from_intent(intent)
    review = runtime.review(draft)
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
            f"  [dim]{review.plan.plan_hash}[/dim]"
        )
        if not confirm("Apply this reviewed topology?"):
            raise typer.Exit(1)

    result = runtime.apply_intent(
        intent,
        expected_plan_hash=review.plan.plan_hash,
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
        fail(
            "V4 topology apply failed.",
            hint="Fix the failed resource and rerun `meridian apply`.",
            hint_type="system",
        )
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

    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("V4 topology intent is missing")
    inspection = SetupRuntime(
        ServerRegistry(SERVER_PROFILES_FILE),
        cluster_loader=lambda: cluster,
    ).inspect_intent(intent)
    resources = [item.action.resource for item in inspection.inspections]
    converged = (
        inspection.converged and cluster.active_plan_hash == inspection.plan_hash and not cluster.pending_plan_hash
    )
    exit_code = 0 if converged else 2
    counts: dict[str, int] = {}
    for resource in resources:
        resource_kind = resource.payload.kind
        counts[resource_kind] = counts.get(resource_kind, 0) + 1
    if json_output:
        summary_text = (
            "V4 topology is converged."
            if converged
            else (f"{len(inspection.drifted)} of {len(resources)} V4 resources require repair.")
        )
        result_data = CompiledPlanResult(
            plan_hash=inspection.plan_hash,
            converged=converged,
            summary=summary_text,
            exit_code=exit_code,
            drifted_resources=[
                CompiledPlanDriftResult(
                    logical_id=item.action.resource.logical_id,
                    error=item.error,
                )
                for item in inspection.drifted
            ],
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
        emit_json(
            command_envelope(
                command="plan",
                data=result_data.to_data(),
                summary=Summary(
                    text=summary_text,
                    changed=not converged,
                    counts=counts,
                ),
                status="no_changes" if converged else "changed",
                exit_code=exit_code,
                timer=operation.timer,
            )
        )
    else:
        state = "[green]converged[/green]" if converged else "[yellow]repair required[/yellow]"
        err_console.print(f"\n  [bold]V4 topology[/bold] — {state}\n  [dim]{inspection.plan_hash}[/dim]\n")
        for kind_name, count in sorted(counts.items()):
            err_console.print(f"  {kind_name}: {count}")
        for item in inspection.drifted:
            detail = f" — {item.error}" if item.error else ""
            err_console.print(f"  [yellow]repair[/yellow] {item.action.resource.logical_id}{detail}")
        err_console.print()
    raise typer.Exit(exit_code)
