"""Finish a reviewed handover and ensure creation seeds through the control service."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from meridian import __version__
from meridian.cluster import ClusterConfig, ClusterConfigExternallyModifiedError
from meridian.control.client import ControlClient, ControlServiceConflict, ControlServiceScopeError
from meridian.control.models import UserCreate, UserEnsure, UserRecord
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.people_seeds import PeopleAdoptionNotice, PeopleSeeds
from meridian.core.version_skew import service_skew


def require_people_service(client: ControlClient) -> None:
    """People writes need a service at least as new as this CLI; the next apply installs it."""
    if not client.has_service_response:
        # A bounded account read also works with people tokens that cannot read fleet health.
        client.get_user("__meridian_version__")
    if service_skew(__version__, client.service_version) in {"service_older", "unknown"}:
        raise MeridianError(
            "The control service needs an upgrade before managing people.",
            hint="Run `meridian apply` once to upgrade and move people to the control service.",
            category="user",
        )


def forget_adopted_people(cluster: ClusterConfig) -> None:
    """Forget adopted identities in memory; other vanished accounts still retire."""
    adoption = cluster.people_adoption
    if adoption is None:
        return
    names = set(adoption.users)
    logical_ids = {f"account:{name.lower()}" for name in names}
    logical_ids.update(
        binding.logical_id
        for binding in cluster.managed_bindings.values()
        if binding.resource_kind == "control_user" and binding.remote_id in names
    )
    cluster.managed_bindings = {
        key: binding
        for key, binding in cluster.managed_bindings.items()
        if not (
            binding.resource_kind == "control_user"
            and (binding.remote_id in names or binding.logical_id in logical_ids)
        )
    }
    cluster.action_checkpoints = {
        key: checkpoint
        for key, checkpoint in cluster.action_checkpoints.items()
        if checkpoint.resource_id not in logical_ids
    }


def ensure_people(client: ControlClient, seeds: PeopleSeeds) -> list[UserRecord]:
    """Create missing seeds and backfill empty names; never replace live settings or names."""
    result = []
    for name in seeds.users:
        name_conflict = False
        person = client.get_user(name)
        shown = seeds.display_names.get(name, "")
        if person is None:
            settings = seeds.creation_settings.get(name)
            create = UserCreate(username=name, display_name=shown, **(settings.to_data() if settings else {}))
            try:
                person = client.create_user(create)
            except ControlServiceConflict as exc:
                # Another invocation may have created this exact seed while this read was in flight.
                if exc.reason == "user_exists":
                    person = client.get_user(name)
                    if person is None:
                        raise
                elif exc.reason == "name_conflict" and shown:
                    name_conflict = True
                    person = client.create_user(UserCreate(**{**create.to_data(), "display_name": ""}))
                else:
                    raise
        if shown and not person.display_name and not name_conflict:
            settings = seeds.creation_settings.get(name)
            wanted = UserEnsure(display_name=shown, **(settings.to_data() if settings else {}))
            try:
                person = client.put_user(name, ensure=wanted)
            except ControlServiceConflict as exc:
                if exc.reason != "name_conflict":
                    raise
                name_conflict = True
        if name_conflict:
            from meridian.console import warn

            warn(f"Kept '{name}' without its legacy display name because that name belongs to another person.")
        result.append(person)
    return result


def resolve_creation_request(client: ControlClient, request: UserCreate) -> UserRecord | None:
    """Resolve a seed with exact username precedence, refusing ambiguous live names."""
    name = request.username or request.display_name
    matches = client.matching_users(name)
    if len(matches) > 1:
        raise MeridianError(
            f"Name '{name}' matches several people: " + ", ".join(user.username for user in matches),
            hint="Use an exact service username in setup.",
            category="user",
        )
    return matches[0] if matches else None


def ensure_creation_requests(client: ControlClient, requests: Sequence[UserCreate]) -> list[UserRecord]:
    """Setup seeds create missing service people, resolving every request against live names."""
    result = []
    for request in requests:
        person = resolve_creation_request(client, request)
        if person is None:
            try:
                person = client.create_user(request)
            except ControlServiceConflict:
                person = resolve_creation_request(client, request)
                if person is None:
                    raise
        result.append(person)
    return result


def finish_people_adoption(
    cluster: ClusterConfig, *, persist: Callable[[ClusterConfig], None] | None = None, read_only: bool = False
) -> None:
    """Resume a handover; an unavailable finish must not block an account read."""
    try:
        _finish_people_adoption(cluster, persist=persist)
    except MeridianError as exc:
        if not read_only:
            raise
        from meridian.console import warn

        warn(f"Could not finish moving people to the control service: {exc}. {exc.hint}")


def _finish_people_adoption(cluster: ClusterConfig, *, persist: Callable[[ClusterConfig], None] | None) -> None:
    """Resume only a committed handover; a partial finish retains every seed for retry."""
    adoption = cluster.people_adoption
    if adoption is None:
        return
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        if adoption.state == "pending":
            # Recover a crash between the service commit and the local committed checkpoint.
            if cluster.pending_generation or not cluster.active_plan_hash:
                return
            try:
                fence = client.fence()
            except ControlServiceScopeError:
                return
            record = fence.record
            if (
                record is None
                or fence.fleet_id != cluster.fleet_id
                or record.generation != cluster.active_generation
                or record.plan_hash != cluster.active_plan_hash
                or record.revision is None
            ):
                return
            try:
                revision = client.revision(record.revision)
            except ControlServiceScopeError:
                return
            if revision is None or revision.status != "applied" or revision.intent.get("access", {}).get("users"):
                return
        require_people_service(client)
        ensure_people(client, adoption)
    bindings, checkpoints = cluster.managed_bindings, cluster.action_checkpoints
    forget_adopted_people(cluster)
    cluster.people_adoption = None
    try:
        (persist or (lambda state: state.save()))(cluster)
    except (ClusterConfigExternallyModifiedError, OSError, ValueError) as exc:
        cluster.people_adoption = adoption
        cluster.managed_bindings, cluster.action_checkpoints = bindings, checkpoints
        raise LocalStateError(
            "People moved to the service, but their adoption checkpoint could not be saved.",
            hint="Repair cluster.yml persistence, then rerun `meridian apply` to finish the checkpoint.",
        ) from exc
    except BaseException:
        cluster.people_adoption = adoption
        cluster.managed_bindings, cluster.action_checkpoints = bindings, checkpoints
        raise


def people_adoption_notice(cluster: ClusterConfig) -> PeopleAdoptionNotice | None:
    adoption = cluster.people_adoption
    if adoption is None:
        return None
    count = len(adoption.users)
    return PeopleAdoptionNotice(
        people=count, message=f"{count} people move to the control service; nothing is deleted."
    )
