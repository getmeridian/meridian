"""Read explicit legacy presentation fields separately from a fleet file's structural edits."""

from typing import Any

from pydantic import ValidationError
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands.fleet_presentation import live_presentation, presentation_client, review_presentation
from meridian.console import err_console
from meridian.core.errors import MeridianError
from meridian.core.fleet_document import IntentChange, change_line
from meridian.core.presentation import PresentationPatch, PresentationState
from meridian.core.server_allowances import ServerMetadata
from meridian.servers import ServerRegistry


def split_presentation(document: dict[str, Any], registry: ServerRegistry) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate explicitly requested service writes before validating the remainder as topology."""
    topology = dict(document)
    delivery = document.get("delivery")
    fields = {
        k: delivery[k]
        for k in ("profile_title", "update_interval_hours", "owner_contact")
        if isinstance(delivery, dict) and k in delivery
    }
    if isinstance(delivery, dict):
        topology["delivery"] = {k: v for k, v in delivery.items() if k not in fields}
    if "server_metadata" in topology:
        metadata = topology.pop("server_metadata")
        if not isinstance(metadata, dict):
            raise MeridianError("server_metadata must map server names to settings.", category="user")
        servers = {}
        for label, value in metadata.items():
            if not isinstance(label, str):
                raise MeridianError("Use a saved text name for each server.", category="user")
            entry = registry.find(label)
            ref = entry.id if entry else label
            if ref in servers:
                raise MeridianError("This file names the same server twice.", category="user")
            servers[ref] = value
        if servers:
            fields["servers"] = servers
    return topology, fields


def presentation_changes(before: PresentationState, after: PresentationState) -> list[IntentChange]:
    changes = []

    def compare(subject: str, target: str, field: str, old: Any, new: Any) -> None:
        if old != new:
            changes.append(
                IntentChange(
                    action="change",
                    subject=subject,
                    target=target,
                    field=field,
                    before=str(old) if old is not None else "",
                    after=str(new) if new is not None else "",
                )
            )

    for key in ("profile_title", "update_interval_hours"):
        compare("delivery", "delivery", key, getattr(before, key), getattr(after, key))
    for key in ("text", "link"):
        compare(
            "delivery",
            "delivery",
            "owner_contact." + key,
            getattr(before.owner_contact, key),
            getattr(after.owner_contact, key),
        )
    for ref, meta in after.servers.items():
        for key in ServerMetadata.model_fields:
            compare("server", ref, key, getattr(before.servers[ref], key), getattr(meta, key))
    return changes


def import_presentation(
    cluster: ClusterConfig, fields: dict[str, Any], *, topology_changed: bool, dry_run: bool, yes: bool
) -> list[IntentChange]:
    """Two independent writes must never silently leave a half-applied import."""
    with presentation_client(cluster) as client:
        before = live_presentation(client)
        try:
            patch = PresentationPatch.model_validate({"expected_version": before.version, **fields})
            changes = presentation_changes(before, patch.updated(before))
        except KeyError as exc:
            raise MeridianError(
                "Save the new server topology first, then import its payment settings.", category="user"
            ) from exc
        except ValidationError as exc:
            raise MeridianError(str(exc.errors()[0]["msg"]), category="user") from exc
        if not changes:
            return []
        if topology_changed:
            raise MeridianError(
                "This file changes both topology and service-owned settings.",
                hint="Import topology without the presentation fields first, then review this file again.",
                category="user",
            )

        def show(_old: PresentationState, _new: PresentationState) -> None:
            for change in changes:
                err_console.print(escape(change_line(change)))

        review_presentation(client, before, patch, dry_run=dry_run, yes=yes, show=show)
        return changes
