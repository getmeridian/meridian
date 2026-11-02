"""Review profile topology together with the people whose effective grants it changes."""

from __future__ import annotations

from typing import Literal

import typer
from pydantic import ValidationError
from rich.markup import escape
from rich.table import Table

from meridian import config, console
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.commands.node_command_results import cut_off_disclosure
from meridian.commands.profile_review import profile_review_paths, require_profile_backend
from meridian.control.client import ControlClient
from meridian.core.access_profile_views import AccessProfileReview
from meridian.core.access_profiles import DEFAULT_ACCESS_PROFILE, AccessProfileIntent
from meridian.core.apply import CompiledApplyPreview, build_compiled_apply_result
from meridian.core.errors import MeridianError
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.profile_commands import ProfileEditResult, ProfileListResult
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.i18n import t
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry


def print_access_review(review: AccessProfileReview) -> None:
    table = Table(title=t("profiles.review_title"))
    for key in ("person", "gained", "lost"):
        table.add_column(t("profiles." + key))
    for person in review.people:
        table.add_row(
            escape(person.username),
            escape(", ".join(person.gained) or t("profiles.none")),
            escape(", ".join(person.lost) or t("profiles.none")),
        )
    console.err_console.print(table)
    console.info(t("profiles.aliases"))
    if review.lost_people:
        console.warn(t("profiles.revocation"))
        console.info(t("profiles.hard_cut_scope"))
        console.info(cut_off_disclosure(review.cut_off))


def run_list() -> None:
    operation = OperationContext()
    with console.error_context("profile.list", timer=operation.timer):
        cluster = load_topology_cluster()
        intent = require_intent(cluster)
        require_profile_backend(intent, requested=True)
        try:
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                result = ProfileListResult(profiles=intent.access_profiles, catalog=client.access_profiles())
        except MeridianError as exc:
            console.fail(exc)
        if console.is_json_mode():
            _emit("profile.list", result, operation, "ok")
            return
        table = Table(title=t("profiles.title"))
        for key in ("name", "grants", "members"):
            table.add_column(t("profiles." + key))
        definitions = {profile.id: profile for profile in intent.access_profiles}
        for profile in result.catalog.profiles:
            grants = (
                t("profiles.all")
                if profile.built_in
                else ", ".join(definitions[profile.id].grants)
                if profile.id in definitions
                else ", ".join(profile.inbound_refs)
            )
            table.add_row(escape(profile.id), escape(grants or t("profiles.none")), str(profile.members))
        console.err_console.print(table)
        console.info(t("profiles.aliases"))


def run_change(
    action: Literal["add", "set", "remove"],
    name: str,
    grants: list[str] | None,
    *,
    empty: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    command, operation = "profile." + action, OperationContext()
    with console.error_context(command, timer=operation.timer):
        if name == DEFAULT_ACCESS_PROFILE:
            console.fail(t("profiles.default_reserved"), hint_type="user")
        if action != "remove" and (bool(grants) == empty):
            console.fail(t("profiles.choose_grants"), hint_type="user")
        try:
            profile = AccessProfileIntent(id=name, grants=grants or [])
        except ValidationError:
            console.fail(t("profiles.invalid"), hint_type="user")
        if console.is_json_mode() and not yes and not dry_run:
            console.fail(t("profiles.requires_yes"), hint_type="user")
        cluster = load_topology_cluster()
        current = require_intent(cluster)
        require_profile_backend(current, requested=True)
        found = next((item for item in current.access_profiles if item.id == name), None)
        if (action == "add") == (found is not None):
            console.fail(t("profiles.exists" if action == "add" else "profiles.unknown", name=name), hint_type="user")
        items = [
            profile if item.id == name and action == "set" else item
            for item in current.access_profiles
            if action != "remove" or item.id != name
        ]
        if action == "add":
            items.append(profile)
        try:
            updated = SetupIntent.model_validate(
                {**current.to_data(), "access_profiles": [item.to_data() for item in items]}
            )
            registry = ServerRegistry(config.SERVER_PROFILES_FILE)
            review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(updated), cluster=cluster)
            from meridian.commands.profile_review import review_profile_access

            access_review = review_profile_access(cluster, review.plan)
        except MeridianError as exc:
            console.fail(exc)
        preview = CompiledApplyPreview(
            plan_hash=review.plan.plan_hash,
            resource_count=len(review.plan.resources),
            review_hash=review.review_hash,
            retiring_resources=[binding.logical_id for binding in review.retiring],
        )
        result = ProfileEditResult(
            profile=profile if action != "remove" else None,
            removed=name if action == "remove" else None,
            review=preview,
            access_review=access_review,
            dry_run=dry_run,
        )
        if updated == current:
            _emit(command, result, operation, "no_changes")
            return
        if not console.is_json_mode() and access_review is not None:
            print_access_review(access_review)
        if dry_run:
            _emit(command, result, operation, "ok")
            return
        if not yes and not console.confirm(t("profiles.confirm", name=name), default=False):
            raise typer.Exit(1)
        execution = apply_reviewed_intent(
            cluster,
            registry,
            updated,
            failure=t("profiles.failed"),
            hint=t("profiles.retry_hint"),
            expected_plan_hash=review.review_hash,
            access_review_hash=access_review.access_hash if access_review is not None else None,
        )
        result = ProfileEditResult.model_validate(
            {
                **result.to_data(),
                "applied": build_compiled_apply_result(execution, exit_code=0, summary="Topology converged."),
            }
        )
        _emit(command, result, operation, "changed")
        if not console.is_json_mode():
            console.ok(t("profiles.changed", name=name))


def _emit(
    command: str, result: ProfileEditResult | ProfileListResult, operation: OperationContext, status: OutputStatus
) -> None:
    if console.is_json_mode():
        emit_json(
            command_envelope(
                command=command,
                data=result.to_data(),
                summary=Summary(text="Access profiles", changed=status == "changed"),
                status=status,
                timer=operation.timer,
            ),
            reveal=profile_review_paths(result.access_review) if isinstance(result, ProfileEditResult) else frozenset(),
        )
