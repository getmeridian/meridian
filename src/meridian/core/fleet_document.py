"""The editable fleet document: its schema, results, and the changes between two intents.

`meridian fleet export` writes a fleet's topology intent with saved server names in
place of profile IDs; `meridian fleet import` reads it back. Everything here is pure:
server lookup and file I/O live in `meridian.setup.fleet_document`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

from meridian.core.apply import CompiledApplyPreview, CompiledApplyResult
from meridian.core.models import CoreModel
from meridian.core.topology import AccessIntent, ExitIntent, SetupIntent

FLEET_DOCUMENT_SCHEMA: Final = "meridian.fleet/v1"
# Transport paths are random shared tokens (`ClusterConfig.secret_values()` masks them in
# logs); the document leaves them out and import carries the saved ones over.
OMITTED_PATH_FIELDS: Final[frozenset[str]] = frozenset({"path"})
ChangeAction = Literal["add", "change", "remove"]

# Top-level intent lists keyed by ID, with the noun a change line uses for one item.
_COLLECTIONS: Final[dict[str, str]] = {
    "exits": "exit",
    "transparent_relays": "relay",
    "routing_gateways": "gateway",
    "egress_pools": "pool",
    "routes": "rule",
}
_SIGNS: Final[dict[str, str]] = {"add": "+", "remove": "-", "change": "~"}
_EMPTY: Final = '""'
# The camouflage SNI is what operators edit most; its line reads `~ exit-2 sni a -> b`.
_SHORT_FIELDS: Final[dict[str, str]] = {"reality_sni": "sni"}


class IntentChange(CoreModel):
    """One difference between the fleet and an imported document, in document terms.

    ``subject`` is the kind of thing (client, exit, path, route, relay, gateway, pool,
    rule, control, access, delivery, fleet); ``target`` names it; a ``change`` also
    names the ``field`` and its rendered ``before`` and ``after`` values.
    """

    action: ChangeAction
    subject: str
    target: str
    field: str = ""
    before: str = ""
    after: str = ""


class FleetExportResult(CoreModel):
    """`meridian fleet export`: the secret-free fleet document, as written to the file."""

    document: dict[str, Any]


class FleetImportResult(CoreModel):
    """`meridian fleet import`: what the file changes, its review, and the apply when it ran."""

    dry_run: bool = False
    changes: list[IntentChange] = Field(default_factory=list)
    review: CompiledApplyPreview | None = None
    applied: CompiledApplyResult | None = None


def change_line(change: IntentChange) -> str:
    """One diff line in the document's own terms: `+ client carol`, `~ exit-2 sni a -> b`."""
    sign = _SIGNS[change.action]
    if change.action != "change":
        return f"{sign} {change.subject} {change.target}"
    if not change.before and not change.after:
        return f"{sign} {change.target} {change.field}"
    return f"{sign} {change.target} {change.field} {change.before or _EMPTY} -> {change.after or _EMPTY}"


def map_server_refs(document: dict[str, Any], rename: Callable[[str], str]) -> None:
    """Rewrite every server reference in an intent-shaped mapping in place.

    Unexpected shapes are left alone, so model validation reports them afterwards.
    """

    def one(value: Any) -> Any:
        return rename(value) if isinstance(value, str) else value

    control = document.get("control")
    if isinstance(control, dict) and "server_ref" in control:
        control["server_ref"] = one(control["server_ref"])
    for key in ("exits", "routing_gateways"):
        for item in _mappings(document.get(key)):
            if "server_ref" in item:
                item["server_ref"] = one(item["server_ref"])
    for relay in _mappings(document.get("transparent_relays")):
        hops = relay.get("hop_server_refs")
        if isinstance(hops, list):
            relay["hop_server_refs"] = [one(hop) for hop in hops]


def intent_server_refs(intent: SetupIntent) -> list[str]:
    """Every server the intent names, in document order, once each."""
    found: list[str] = []

    def note(ref: str) -> str:
        found.append(ref)
        return ref

    map_server_refs(intent.model_dump(mode="json"), note)
    return list(dict.fromkeys(found))


def relabel_servers(intent: SetupIntent, labels: Mapping[str, str]) -> SetupIntent:
    """The same intent with server references shown by their labels (unknown refs stay)."""
    data = intent.model_dump(mode="json")
    map_server_refs(data, lambda ref: labels.get(ref, ref))
    return SetupIntent.model_validate(data)


def document_paths(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The protocol-path mappings of every exit in an intent-shaped mapping."""
    return [path for exit_ in _mappings(document.get("exits")) for path in _mappings(exit_.get("paths"))]


def intent_changes(before: SetupIntent, after: SetupIntent) -> list[IntentChange]:
    """What turns ``before`` into ``after``: additions, removals, then field changes, in document order.

    Transport paths are never compared (the document omits them). Two intents differ
    only if this returns a change; an edit nothing else names reports a reorder.
    """
    changes: list[IntentChange] = []
    for name in SetupIntent.model_fields:
        old, new = getattr(before, name), getattr(after, name)
        if old == new:
            continue
        if name == "access":
            changes.extend(_access_changes(old, new))
        elif name in _COLLECTIONS:
            changes.extend(_collection_changes(_COLLECTIONS[name], name, old, new))
        elif isinstance(old, BaseModel):
            changes.extend(_field_changes(name, name, old, new))
        else:
            changes.append(_change(name="fleet", target="fleet", field=name, before=old, after=new))
    if not changes and _without_paths(before) != _without_paths(after):
        changes.append(IntentChange(action="change", subject="fleet", target="fleet", field="order"))
    return changes


def _access_changes(before: AccessIntent, after: AccessIntent) -> list[IntentChange]:
    changes = [
        IntentChange(action="remove", subject="client", target=user) for user in before.users if user not in after.users
    ]
    changes += [
        IntentChange(action="add", subject="client", target=user) for user in after.users if user not in before.users
    ]
    for user in after.users:
        if user in before.users and before.display_name(user) != after.display_name(user):
            changes.append(
                _change(
                    name="client",
                    target=user,
                    field="name",
                    before=before.display_name(user),
                    after=after.display_name(user),
                )
            )
        old_settings = before.creation_settings.get(user)
        new_settings = after.creation_settings.get(user)
        if old_settings != new_settings:
            changes.append(
                _change(
                    name="client",
                    target=user,
                    field="creation_settings",
                    before=old_settings.model_dump(mode="json") if old_settings else {},
                    after=new_settings.model_dump(mode="json") if new_settings else {},
                )
            )
    if before.squad_name != after.squad_name:
        changes.append(
            _change(
                name="access", target="access", field="squad_name", before=before.squad_name, after=after.squad_name
            )
        )
    changes += _order_change("access", "access", "users", before.users, after.users)
    return changes


def _collection_changes(
    subject: str, label: str, before: Sequence[BaseModel], after: Sequence[BaseModel], *, parent: str = ""
) -> list[IntentChange]:
    old = {_key(item): item for item in before}
    new = {_key(item): item for item in after}
    prefix = f"{parent} " if parent else ""
    changes = [IntentChange(action="remove", subject=subject, target=prefix + key) for key in old if key not in new]
    changes += [IntentChange(action="add", subject=subject, target=prefix + key) for key in new if key not in old]
    for key, item in new.items():
        if key in old and old[key] != item:
            previous = old[key]
            if isinstance(previous, ExitIntent) and isinstance(item, ExitIntent):
                changes.extend(_exit_changes(previous, item))
            else:
                # A path's lines name its exit; the field carries the protocol.
                target = parent if subject == "path" else prefix + key
                changes.extend(_field_changes(subject, target, previous, item))
    changes += _order_change(subject, parent or label, f"{label} order" if parent else "order", list(old), list(new))
    return changes


def _exit_changes(before: ExitIntent, after: ExitIntent) -> list[IntentChange]:
    changes = _field_changes("exit", after.id, before, after, skip={"paths", "egress_overrides"})
    changes += _collection_changes("path", "paths", before.paths, after.paths, parent=after.id)
    changes += _collection_changes(
        "route", "egress_overrides", before.egress_overrides, after.egress_overrides, parent=after.id
    )
    return changes


def _field_changes(
    subject: str, target: str, before: BaseModel, after: BaseModel, *, skip: frozenset[str] | set[str] = frozenset()
) -> list[IntentChange]:
    """Changed leaf fields; a protocol path's lines name its exit and protocol, as in `~ exit-2 xhttp host a -> b`."""
    path_protocol = getattr(after, "protocol", "") if subject == "path" else ""
    changes: list[IntentChange] = []
    for name in type(after).model_fields:
        if name in skip or name in OMITTED_PATH_FIELDS or (name == "id" and not path_protocol):
            continue
        old, new = getattr(before, name), getattr(after, name)
        if old == new:
            continue
        field = _SHORT_FIELDS.get(name, name)
        if path_protocol and not (path_protocol == "reality" and field == "sni"):
            field = f"{path_protocol} {field}"
        changes.append(_change(name=subject, target=target, field=field, before=old, after=new))
    return changes


def _order_change(
    subject: str, target: str, field: str, before: Sequence[str], after: Sequence[str]
) -> list[IntentChange]:
    kept_before = [item for item in before if item in after]
    kept_after = [item for item in after if item in before]
    if kept_before == kept_after:
        return []
    return [_change(name=subject, target=target, field=field, before=kept_before, after=kept_after)]


def _change(*, name: str, target: str, field: str, before: Any, after: Any) -> IntentChange:
    return IntentChange(
        action="change", subject=name, target=target, field=field, before=_render(before), after=_render(after)
    )


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(_render(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={_render(item)}" for key, item in value.items())
    return str(value)


def _key(item: BaseModel) -> str:
    """Paths are keyed by protocol (one per exit), everything else by ID."""
    protocol = getattr(item, "protocol", None)
    return str(protocol if protocol is not None else getattr(item, "id"))


def _without_paths(intent: SetupIntent) -> dict[str, Any]:
    data = intent.model_dump(mode="json")
    for path in document_paths(data):
        for field in OMITTED_PATH_FIELDS:
            path.pop(field, None)
    return data


def _mappings(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
